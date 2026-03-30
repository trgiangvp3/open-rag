using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Data;

public class AppDbContext(DbContextOptions<AppDbContext> options) : DbContext(options)
{
    public DbSet<Collection> Collections => Set<Collection>();
    public DbSet<Document> Documents => Set<Document>();
    public DbSet<ChatSession> ChatSessions => Set<ChatSession>();
    public DbSet<ChatMessageEntity> ChatMessages => Set<ChatMessageEntity>();
    public DbSet<AppSetting> AppSettings => Set<AppSetting>();
    public DbSet<Domain> Domains => Set<Domain>();

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.Entity<AppSetting>(e =>
        {
            e.HasKey(s => s.Key);
        });
        modelBuilder.Entity<Collection>(e =>
        {
            e.HasIndex(c => c.Name).IsUnique();
        });

        modelBuilder.Entity<Domain>(e =>
        {
            e.HasOne(d => d.Parent)
             .WithMany(d => d.Children)
             .HasForeignKey(d => d.ParentId)
             .OnDelete(DeleteBehavior.Restrict);
            e.HasIndex(d => d.Slug).IsUnique();
        });

        modelBuilder.Entity<Document>(e =>
        {
            e.HasOne(d => d.Collection)
             .WithMany(c => c.Documents)
             .HasForeignKey(d => d.CollectionId)
             .OnDelete(DeleteBehavior.Cascade);

            e.HasOne(d => d.Domain)
             .WithMany(d => d.Documents)
             .HasForeignKey(d => d.DomainId)
             .OnDelete(DeleteBehavior.SetNull);

            e.HasIndex(d => d.Status);
            e.HasIndex(d => d.CollectionId);
            e.HasIndex(d => d.DomainId);
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

        // Seed domains (2-level taxonomy)
        modelBuilder.Entity<Domain>().HasData(
            // Level 1
            new Domain { Id = 1, Name = "Ngân hàng - Tín dụng", Slug = "ngan-hang-tin-dung" },
            new Domain { Id = 2, Name = "Tài chính", Slug = "tai-chinh" },
            new Domain { Id = 3, Name = "Doanh nghiệp", Slug = "doanh-nghiep" },
            new Domain { Id = 4, Name = "Đầu tư", Slug = "dau-tu" },
            new Domain { Id = 5, Name = "Công nghệ thông tin", Slug = "cong-nghe-thong-tin" },
            // Level 2 — Ngân hàng
            new Domain { Id = 10, Name = "Quản lý ngoại hối", ParentId = 1, Slug = "quan-ly-ngoai-hoi" },
            new Domain { Id = 11, Name = "Thanh toán", ParentId = 1, Slug = "thanh-toan" },
            new Domain { Id = 12, Name = "Mạng lưới hoạt động", ParentId = 1, Slug = "mang-luoi-hoat-dong" },
            new Domain { Id = 13, Name = "Xếp hạng - Giám sát", ParentId = 1, Slug = "xep-hang-giam-sat" },
            new Domain { Id = 14, Name = "Phân loại nợ - Trích lập dự phòng", ParentId = 1, Slug = "phan-loai-no" },
            new Domain { Id = 15, Name = "Cho vay", ParentId = 1, Slug = "cho-vay" },
            new Domain { Id = 16, Name = "Cấp phép", ParentId = 1, Slug = "cap-phep" },
            // Level 2 — Tài chính
            new Domain { Id = 20, Name = "Thuế - Phí - Lệ phí", ParentId = 2, Slug = "thue-phi-le-phi" },
            new Domain { Id = 21, Name = "Kế toán - Kiểm toán", ParentId = 2, Slug = "ke-toan-kiem-toan" },
            new Domain { Id = 22, Name = "Ngân sách nhà nước", ParentId = 2, Slug = "ngan-sach-nha-nuoc" },
            // Level 2 — Doanh nghiệp
            new Domain { Id = 30, Name = "Thành lập - Đăng ký", ParentId = 3, Slug = "thanh-lap-dang-ky" },
            new Domain { Id = 31, Name = "Quản trị - Điều hành", ParentId = 3, Slug = "quan-tri-dieu-hanh" },
            // Level 2 — Đầu tư
            new Domain { Id = 40, Name = "Đầu tư nước ngoài", ParentId = 4, Slug = "dau-tu-nuoc-ngoai" },
            new Domain { Id = 41, Name = "Chứng khoán", ParentId = 4, Slug = "chung-khoan" },
            // Level 2 — CNTT
            new Domain { Id = 50, Name = "An toàn thông tin", ParentId = 5, Slug = "an-toan-thong-tin" },
            new Domain { Id = 51, Name = "Giao dịch điện tử", ParentId = 5, Slug = "giao-dich-dien-tu" }
        );

        // Seed default LLM settings
        modelBuilder.Entity<AppSetting>().HasData(
            new AppSetting { Key = "Llm:BaseUrl", Value = "" },
            new AppSetting { Key = "Llm:ApiKey", Value = "" },
            new AppSetting { Key = "Llm:Model", Value = "gpt-4o-mini" },
            new AppSetting { Key = "Llm:Temperature", Value = "0.2" },
            new AppSetting { Key = "Llm:MaxTokens", Value = "2048" },
            new AppSetting { Key = "Llm:SystemPrompt", Value = "" }
        );
    }
}
