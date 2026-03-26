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

    /// <summary>
    /// Generate an answer from retrieved chunks, optionally including conversation history.
    /// Returns null if LLM is not configured.
    /// </summary>
    public async Task<GenerateResult?> GenerateAsync(
        string query,
        List<ChunkResult> chunks,
        List<ChatMessage>? history = null,
        CancellationToken ct = default)
    {
        var settings = await appSettings.GetAllAsync(ct);

        var apiKey = settings.GetValueOrDefault("Llm:ApiKey", "");
        if (string.IsNullOrWhiteSpace(apiKey)) return null;

        var baseUrl = settings.GetValueOrDefault("Llm:BaseUrl", "");
        var model = settings.GetValueOrDefault("Llm:Model", "gpt-4o-mini");
        var temperatureStr = settings.GetValueOrDefault("Llm:Temperature", "0.2");
        var maxTokensStr = settings.GetValueOrDefault("Llm:MaxTokens", "2048");
        var customSystemPrompt = settings.GetValueOrDefault("Llm:SystemPrompt", "");

        if (!double.TryParse(temperatureStr, System.Globalization.CultureInfo.InvariantCulture, out var temperature))
            temperature = 0.2;
        if (!int.TryParse(maxTokensStr, out var maxTokens))
            maxTokens = 2048;

        // Cap chunks to avoid exceeding model context limits
        var contextChunks = chunks.Take(8).ToList();

        var contextBuilder = new StringBuilder();
        for (int i = 0; i < contextChunks.Count; i++)
            contextBuilder.AppendLine($"[{i + 1}] {contextChunks[i].Text}");

        var systemPrompt = string.IsNullOrWhiteSpace(customSystemPrompt)
            ? $"""
                You are a helpful assistant. Answer the user's question based solely on the provided context.
                When referencing information from the context, cite the source number like [1], [2], etc.
                If the context does not contain enough information to answer, say so clearly.

                Context:
                {contextBuilder}
                """
            : $"""
                {customSystemPrompt}

                Context:
                {contextBuilder}
                """;

        var messages = new List<object> { new { role = "system", content = systemPrompt } };

        if (history is not null)
            foreach (var msg in history)
                messages.Add(new { role = msg.Role, content = msg.Content });

        messages.Add(new { role = "user", content = query });

        var requestBody = new
        {
            model,
            messages,
            temperature,
            max_tokens = maxTokens,
        };

        try
        {
            // Build the full URL per-request since BaseUrl is dynamic
            var requestUrl = string.IsNullOrWhiteSpace(baseUrl)
                ? "/chat/completions"
                : $"{baseUrl.TrimEnd('/')}/chat/completions";

            using var request = new HttpRequestMessage(HttpMethod.Post, requestUrl);
            request.Content = JsonContent.Create(requestBody);
            request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", apiKey);

            var response = await http.SendAsync(request, ct);
            response.EnsureSuccessStatusCode();

            var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
            var answerText = result
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString() ?? "";

            // Extract citation indices [1], [2] -> 0-based
            var citations = Regex.Matches(answerText, @"\[(\d+)\]")
                .Select(m => int.Parse(m.Groups[1].Value) - 1)
                .Where(i => i >= 0 && i < contextChunks.Count)
                .Distinct()
                .OrderBy(i => i)
                .ToList();

            return new GenerateResult(answerText, citations);
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "LLM generation failed");
            return null;
        }
    }
}
