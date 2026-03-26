using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Dto.Shared;

namespace OpenRAG.Api.Services;

public record GenerateResult(string Answer, List<int> Citations);

public class LlmClient(HttpClient http, AppSettingsService appSettings, ILogger<LlmClient> logger)
{
    public bool IsEnabled
    {
        get
        {
            var apiKey = appSettings.GetAsync("Llm:ApiKey").GetAwaiter().GetResult();
            return !string.IsNullOrWhiteSpace(apiKey);
        }
    }

    // ── RAG Answer Generation ────────────────────────────────────────────

    public async Task<GenerateResult?> GenerateAsync(
        string query,
        List<ChunkResult> chunks,
        List<ChatMessage>? history = null,
        CancellationToken ct = default)
    {
        var settings = await GetSettingsAsync(ct);
        if (settings is null) return null;

        var contextChunks = chunks.Take(8).ToList();
        var contextBuilder = new StringBuilder();
        for (int i = 0; i < contextChunks.Count; i++)
            contextBuilder.AppendLine($"[{i + 1}] {contextChunks[i].Text}");

        var systemPrompt = string.IsNullOrWhiteSpace(settings.SystemPrompt)
            ? $"""
                You are a helpful assistant. Answer the user's question based solely on the provided context.
                When referencing information from the context, cite the source number like [1], [2], etc.
                If the context does not contain enough information to answer, say so clearly.

                Context:
                {contextBuilder}
                """
            : $"""
                {settings.SystemPrompt}

                Context:
                {contextBuilder}
                """;

        var messages = new List<object> { new { role = "system", content = systemPrompt } };
        if (history is not null)
            foreach (var msg in history)
                messages.Add(new { role = msg.Role, content = msg.Content });
        messages.Add(new { role = "user", content = query });

        var answerText = await CallLlmAsync(settings, messages, settings.MaxTokens, ct);
        if (answerText is null) return null;

        var citations = Regex.Matches(answerText, @"\[(\d+)\]")
            .Select(m => int.Parse(m.Groups[1].Value) - 1)
            .Where(i => i >= 0 && i < contextChunks.Count)
            .Distinct()
            .OrderBy(i => i)
            .ToList();

        return new GenerateResult(answerText, citations);
    }

    // ── Multi-Query Expansion ────────────────────────────────────────────

    public async Task<List<string>?> GenerateQueriesAsync(string query, int count = 3, CancellationToken ct = default)
    {
        var settings = await GetSettingsAsync(ct);
        if (settings is null) return null;

        var messages = new List<object>
        {
            new { role = "system", content = $"""
                You are a search query optimizer. Given a user question, generate {count} alternative search queries
                that could help find relevant information. Each query should approach the topic from a different angle
                or use different terminology.

                Return ONLY the queries, one per line, no numbering, no explanation.
                """ },
            new { role = "user", content = query }
        };

        var result = await CallLlmAsync(settings, messages, maxTokens: 300, ct);
        if (result is null) return null;

        return result.Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(l => l.Length > 5 && !l.StartsWith('-') && !l.StartsWith("*"))
            .Select(l => Regex.Replace(l, @"^\d+[\.\)]\s*", "").Trim())
            .Where(l => l.Length > 5)
            .Take(count)
            .ToList();
    }

    // ── HyDE (Hypothetical Document Embedding) ──────────────────────────

    public async Task<string?> GenerateHypotheticalAsync(string query, CancellationToken ct = default)
    {
        var settings = await GetSettingsAsync(ct);
        if (settings is null) return null;

        var messages = new List<object>
        {
            new { role = "system", content = """
                You are a document generator. Given a user question, write a short passage (150-300 words)
                that would be a perfect answer to this question, as if it were extracted from an authoritative document.

                Write in the same language as the question.
                Write in a formal, document-like style (not conversational).
                Do not say "according to" or reference any source — write AS IF you are the source document.
                """ },
            new { role = "user", content = query }
        };

        return await CallLlmAsync(settings, messages, maxTokens: 500, ct);
    }

    // ── Internal helpers ─────────────────────────────────────────────────

    private record LlmSettings(string BaseUrl, string ApiKey, string Model, double Temperature, int MaxTokens, string SystemPrompt);

    private async Task<LlmSettings?> GetSettingsAsync(CancellationToken ct)
    {
        var all = await appSettings.GetAllAsync(ct);
        var apiKey = all.GetValueOrDefault("Llm:ApiKey", "");
        if (string.IsNullOrWhiteSpace(apiKey)) return null;

        if (!double.TryParse(all.GetValueOrDefault("Llm:Temperature", "0.2"),
            System.Globalization.CultureInfo.InvariantCulture, out var temperature))
            temperature = 0.2;
        if (!int.TryParse(all.GetValueOrDefault("Llm:MaxTokens", "2048"), out var maxTokens))
            maxTokens = 2048;

        return new LlmSettings(
            BaseUrl: all.GetValueOrDefault("Llm:BaseUrl", ""),
            ApiKey: apiKey,
            Model: all.GetValueOrDefault("Llm:Model", "gpt-4o-mini"),
            Temperature: temperature,
            MaxTokens: maxTokens,
            SystemPrompt: all.GetValueOrDefault("Llm:SystemPrompt", "")
        );
    }

    private async Task<string?> CallLlmAsync(LlmSettings settings, List<object> messages, int maxTokens, CancellationToken ct)
    {
        try
        {
            var requestBody = new
            {
                model = settings.Model,
                messages,
                temperature = settings.Temperature,
                max_tokens = maxTokens,
            };

            var requestUrl = string.IsNullOrWhiteSpace(settings.BaseUrl)
                ? "/chat/completions"
                : $"{settings.BaseUrl.TrimEnd('/')}/chat/completions";

            using var request = new HttpRequestMessage(HttpMethod.Post, requestUrl);
            request.Content = JsonContent.Create(requestBody);
            request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", settings.ApiKey);

            var response = await http.SendAsync(request, ct);
            response.EnsureSuccessStatusCode();

            var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
            return result.GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString() ?? "";
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "LLM call failed");
            return null;
        }
    }
}
