namespace OpenRAG.Api.Models.Dto.Shared;

/// <summary>A single turn in a conversation (user or assistant).</summary>
public record ChatMessage(string Role, string Content);
