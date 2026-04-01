using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace OpenRAG.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddContentHash : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "ContentHash",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_Documents_ContentHash",
                table: "Documents",
                column: "ContentHash");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_Documents_ContentHash",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "ContentHash",
                table: "Documents");
        }
    }
}
