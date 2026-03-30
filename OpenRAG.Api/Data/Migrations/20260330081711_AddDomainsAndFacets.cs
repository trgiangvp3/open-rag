using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

#pragma warning disable CA1814 // Prefer jagged arrays over multidimensional

namespace OpenRAG.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddDomainsAndFacets : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<int>(
                name: "DomainId",
                table: "Documents",
                type: "INTEGER",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "SubjectsJson",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "SuggestedDomainsJson",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "Domains",
                columns: table => new
                {
                    Id = table.Column<int>(type: "INTEGER", nullable: false)
                        .Annotation("Sqlite:Autoincrement", true),
                    Name = table.Column<string>(type: "TEXT", nullable: false),
                    ParentId = table.Column<int>(type: "INTEGER", nullable: true),
                    Slug = table.Column<string>(type: "TEXT", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_Domains", x => x.Id);
                    table.ForeignKey(
                        name: "FK_Domains_Domains_ParentId",
                        column: x => x.ParentId,
                        principalTable: "Domains",
                        principalColumn: "Id",
                        onDelete: ReferentialAction.Restrict);
                });

            migrationBuilder.InsertData(
                table: "Domains",
                columns: new[] { "Id", "Name", "ParentId", "Slug" },
                values: new object[,]
                {
                    { 1, "Ngân hàng - Tín dụng", null, "ngan-hang-tin-dung" },
                    { 2, "Tài chính", null, "tai-chinh" },
                    { 3, "Doanh nghiệp", null, "doanh-nghiep" },
                    { 4, "Đầu tư", null, "dau-tu" },
                    { 5, "Công nghệ thông tin", null, "cong-nghe-thong-tin" },
                    { 10, "Quản lý ngoại hối", 1, "quan-ly-ngoai-hoi" },
                    { 11, "Thanh toán", 1, "thanh-toan" },
                    { 12, "Mạng lưới hoạt động", 1, "mang-luoi-hoat-dong" },
                    { 13, "Xếp hạng - Giám sát", 1, "xep-hang-giam-sat" },
                    { 14, "Phân loại nợ - Trích lập dự phòng", 1, "phan-loai-no" },
                    { 15, "Cho vay", 1, "cho-vay" },
                    { 16, "Cấp phép", 1, "cap-phep" },
                    { 20, "Thuế - Phí - Lệ phí", 2, "thue-phi-le-phi" },
                    { 21, "Kế toán - Kiểm toán", 2, "ke-toan-kiem-toan" },
                    { 22, "Ngân sách nhà nước", 2, "ngan-sach-nha-nuoc" },
                    { 30, "Thành lập - Đăng ký", 3, "thanh-lap-dang-ky" },
                    { 31, "Quản trị - Điều hành", 3, "quan-tri-dieu-hanh" },
                    { 40, "Đầu tư nước ngoài", 4, "dau-tu-nuoc-ngoai" },
                    { 41, "Chứng khoán", 4, "chung-khoan" },
                    { 50, "An toàn thông tin", 5, "an-toan-thong-tin" },
                    { 51, "Giao dịch điện tử", 5, "giao-dich-dien-tu" }
                });

            migrationBuilder.CreateIndex(
                name: "IX_Documents_DomainId",
                table: "Documents",
                column: "DomainId");

            migrationBuilder.CreateIndex(
                name: "IX_Domains_ParentId",
                table: "Domains",
                column: "ParentId");

            migrationBuilder.CreateIndex(
                name: "IX_Domains_Slug",
                table: "Domains",
                column: "Slug",
                unique: true);

            migrationBuilder.AddForeignKey(
                name: "FK_Documents_Domains_DomainId",
                table: "Documents",
                column: "DomainId",
                principalTable: "Domains",
                principalColumn: "Id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_Documents_Domains_DomainId",
                table: "Documents");

            migrationBuilder.DropTable(
                name: "Domains");

            migrationBuilder.DropIndex(
                name: "IX_Documents_DomainId",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "DomainId",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "SubjectsJson",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "SuggestedDomainsJson",
                table: "Documents");
        }
    }
}
