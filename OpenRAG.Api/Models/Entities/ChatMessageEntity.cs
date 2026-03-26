namespace OpenRAG.Api.Models.Entities;

/// <summary>A persisted chat message. Named *Entity to avoid collision with the ChatMessage DTO.</summary>
public class ChatMessageEntity
{
    public int Id { get; set; }
    public Guid SessionId { get; set; }
    public ChatSession Session { get; set; } = null!;
    public string Role { get; set; } = "";      // "user" | "assistant"
    public string Content { get; set; } = "";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
}
