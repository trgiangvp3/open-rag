using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using OpenRAG.Api.Models.Dto.Responses;
using OpenRAG.Api.Models.Dto.Shared;

namespace OpenRAG.Api.Services;

public record GenerateResult(string Answer, List<int> Citations);

public class LlmClient(HttpClient http, IConfiguration config, ILogger<LlmClient> logger)
{
    public bool IsEnabled => !string.IsNullOrWhiteSpace(config["Llm:ApiKey"]);

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
        if (!IsEnabled) return null;

        // Cap chunks to avoid exceeding model context limits
        var contextChunks = chunks.Take(8).ToList();

        var contextBuilder = new StringBuilder();
        for (int i = 0; i < contextChunks.Count; i++)
            contextBuilder.AppendLine($"[{i + 1}] {contextChunks[i].Text}");

        var systemPrompt = $"""
            You are a helpful assistant. Answer the user's question based solely on the provided context.
            When referencing information from the context, cite the source number like [1], [2], etc.
            If the context does not contain enough information to answer, say so clearly.

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
            model = config["Llm:Model"] ?? "gpt-4o-mini",
            messages,
            temperature = 0.2,
        };

        try
        {
            var response = await http.PostAsJsonAsync("/chat/completions", requestBody, ct);
            response.EnsureSuccessStatusCode();

            var result = await response.Content.ReadFromJsonAsync<JsonElement>(ct);
            var answerText = result
                .GetProperty("choices")[0]
                .GetProperty("message")
                .GetProperty("content")
                .GetString() ?? "";

            // Extract citation indices [1], [2] → 0-based
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
