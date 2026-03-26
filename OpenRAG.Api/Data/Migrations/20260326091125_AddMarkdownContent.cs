using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace OpenRAG.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddMarkdownContent : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "MarkdownContent",
                table: "Documents",
                type: "TEXT",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "MarkdownContent",
                table: "Documents");
        }
    }
}
