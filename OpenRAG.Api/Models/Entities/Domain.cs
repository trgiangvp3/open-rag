namespace OpenRAG.Api.Models.Entities;

public class Domain
{
    public int Id { get; set; }
    public string Name { get; set; } = "";
    public int? ParentId { get; set; }
    public Domain? Parent { get; set; }
    public string Slug { get; set; } = "";
    public ICollection<Domain> Children { get; set; } = [];
    public ICollection<Document> Documents { get; set; } = [];
}
