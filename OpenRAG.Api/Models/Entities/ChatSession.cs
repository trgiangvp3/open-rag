namespace OpenRAG.Api.Models.Entities;

public class ChatSession
{
    public Guid Id { get; set; } = Guid.NewGuid();
    public string Collection { get; set; } = "documents";
    public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    public DateTime UpdatedAt { get; set; } = DateTime.UtcNow;

    public ICollection<ChatMessageEntity> Messages { get; set; } = [];
}
