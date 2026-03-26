using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Data;

public class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options)
{
    public DbSet<Collection> Collections => Set<Collection>();
    public DbSet<Document> Documents => Set<Document>();
    public DbSet<ChatSession> ChatSessions => Set<ChatSession>();
    public DbSet<ChatMessageEntity> ChatMessages => Set<ChatMessageEntity>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<Collection>(e =>
        {
            e.HasIndex(c => c.Name).IsUnique();
        });

        modelBuilder.Entity<Document>(e =>
        {
            e.HasOne(d => d.Collection)
             .WithMany(c => c.Documents)
             .HasForeignKey(d => d.CollectionId)
             .OnDelete(DeleteBehavior.Cascade);

            e.HasIndex(d => d.Status);
            e.HasIndex(d => d.CollectionId);
        });

        modelBuilder.Entity<ChatMessageEntity>(e =>
        {
            e.HasOne(m => m.Session)
             .WithMany(s => s.Messages)
             .HasForeignKey(m => m.SessionId)
             .OnDelete(DeleteBehavior.Cascade);

            e.HasIndex(m => m.SessionId);
        });

        // Seed default collection
        modelBuilder.Entity<Collection>().HasData(new Collection
        {
            Id = 1,
            Name = "documents",
            Description = "Default collection",
            CreatedAt = new DateTime(2024, 1, 1, 0, 0, 0, DateTimeKind.Utc),
        });
    }
}
