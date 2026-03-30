using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace OpenRAG.Api.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddDocumentLegalMetadata : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "DocumentNumber",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "DocumentTitle",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "DocumentType",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "DocumentTypeDisplay",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<DateTime>(
                name: "EffectiveDate",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<DateTime>(
                name: "IssuedDate",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "IssuingAuthority",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "LegalBasisJson",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "ReferencedDocsJson",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "SignedLocation",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "Tags",
                table: "Documents",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "TerminologyJson",
                table: "Documents",
                type: "TEXT",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "DocumentNumber",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "DocumentTitle",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "DocumentType",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "DocumentTypeDisplay",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "EffectiveDate",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "IssuedDate",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "IssuingAuthority",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "LegalBasisJson",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "ReferencedDocsJson",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "SignedLocation",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "Tags",
                table: "Documents");

            migrationBuilder.DropColumn(
                name: "TerminologyJson",
                table: "Documents");
        }
    }
}
