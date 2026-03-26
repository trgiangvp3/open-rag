using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace OpenRAG.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddChunkingConfig : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<bool>(
                name: "AutoDetectHeadings",
                table: "Collections",
                type: "INTEGER",
                nullable: false,
                defaultValue: true);

            migrationBuilder.AddColumn<int>(
                name: "ChunkOverlap",
                table: "Collections",
                type: "INTEGER",
                nullable: false,
                defaultValue: 50);

            migrationBuilder.AddColumn<int>(
                name: "ChunkSize",
                table: "Collections",
                type: "INTEGER",
                nullable: false,
                defaultValue: 400);

            migrationBuilder.AddColumn<string>(
                name: "HeadingScript",
                table: "Collections",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "SectionTokenThreshold",
                table: "Collections",
                type: "INTEGER",
                nullable: false,
                defaultValue: 800);

            migrationBuilder.UpdateData(
                table: "Collections",
                keyColumn: "Id",
                keyValue: 1,
                columns: new[] { "AutoDetectHeadings", "ChunkOverlap", "ChunkSize", "HeadingScript", "SectionTokenThreshold" },
                values: new object[] { true, 50, 400, null, 800 });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "AutoDetectHeadings",
                table: "Collections");

            migrationBuilder.DropColumn(
                name: "ChunkOverlap",
                table: "Collections");

            migrationBuilder.DropColumn(
                name: "ChunkSize",
                table: "Collections");

            migrationBuilder.DropColumn(
                name: "HeadingScript",
                table: "Collections");

            migrationBuilder.DropColumn(
                name: "SectionTokenThreshold",
                table: "Collections");
        }
    }
}
