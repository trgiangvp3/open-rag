using System.Text.RegularExpressions;
using OpenRAG.Api.Services.Parsing;
using SharpToken;

namespace OpenRAG.Api.Services.Chunking;

/// <summary>
/// Chunks Vietnamese legal documents (VBQPPL) by their structural hierarchy:
/// Chương (Chapter) > Mục (Section) > Điều (Article) > Khoản (Clause) > Điểm (Point).
///
/// Chunk text includes section path as context prefix (contains topic keywords).
/// Document-level info (type, number, authority) is in metadata only — not in text.
/// </summary>
public partial class LegalDocumentChunker : IChunker
{
    private readonly int _chunkSize;
    private readonly int _chunkOverlap;
    private readonly LegalDocumentMetadata _metadata;
    private readonly GptEncoding _encoding;

    [GeneratedRegex(@"(?=(?:^|\n)\d+\.\s)", RegexOptions.Multiline)]
    private static partial Regex ClauseSplitRegex();

    [GeneratedRegex(@"(?=(?:^|\n)[a-zđ]\)\s)", RegexOptions.Multiline)]
    private static partial Regex PointSplitRegex();

    private static readonly Regex SentenceSplitPattern = new(@"(?<=[.!?;])\s+|\n+");

    public LegalDocumentChunker(LegalDocumentMetadata metadata, int chunkSize = 400, int chunkOverlap = 50)
    {
        _metadata = metadata;
        _chunkSize = chunkSize;
        _chunkOverlap = chunkOverlap;
        _encoding = GptEncoding.GetEncoding("cl100k_base");
    }

    /// <summary>
    /// The text parameter is ignored — chunking is based on the parsed LegalDocumentMetadata.
    /// </summary>
    public List<Chunk> Chunk(string text, Dictionary<string, string>? metadata = null)
    {
        metadata ??= [];
        var chunks = new List<Chunk>();

        // 1. Legal basis chunk
        if (_metadata.LegalBases.Count > 0)
        {
            var basisText = string.Join("\n", _metadata.LegalBases.Select(b => b.Text));
            var fullText = $"Căn cứ pháp lý\n---\n{basisText}";
            var meta = BuildChunkMeta(metadata, "Căn cứ pháp lý", "legal_basis");
            chunks.Add(new Chunk(fullText, chunks.Count, meta));
        }

        // 2. Terminology — each term as its own chunk
        foreach (var term in _metadata.Terminology)
        {
            var fullText = $"Giải thích từ ngữ\n---\n{term.Term}: {term.Definition}";
            var meta = BuildChunkMeta(metadata, "Giải thích từ ngữ", "terminology");
            meta["term"] = term.Term;
            chunks.Add(new Chunk(fullText, chunks.Count, meta));
        }

        // 3. Articles — main content
        foreach (var section in _metadata.Sections)
            ChunkSection(section, chunks, metadata);

        return chunks;
    }

    private void ChunkSection(LegalSection section, List<Chunk> chunks,
        Dictionary<string, string> baseMeta)
    {
        switch (section.Type)
        {
            case "chapter":
            case "section":
                if (section.Children.Count > 0)
                {
                    foreach (var child in section.Children)
                        ChunkSection(child, chunks, baseMeta);
                }
                else if (!string.IsNullOrWhiteSpace(section.Content))
                {
                    EmitChunks(section.Content, section.Path, section.Type, chunks, baseMeta);
                }
                break;

            case "article":
                ChunkArticle(section, chunks, baseMeta);
                break;

            case "appendix":
                if (section.Children.Count > 0)
                {
                    foreach (var child in section.Children)
                        ChunkSection(child, chunks, baseMeta);
                }
                else if (!string.IsNullOrWhiteSpace(section.Content))
                {
                    EmitChunks(section.Content, section.Path, "appendix", chunks, baseMeta);
                }
                break;
        }
    }

    private void ChunkArticle(LegalSection article, List<Chunk> chunks,
        Dictionary<string, string> baseMeta)
    {
        // Skip terminology article — already handled separately
        if (article.Title.Contains("Giải thích từ ngữ", StringComparison.OrdinalIgnoreCase)
            && _metadata.Terminology.Count > 0)
            return;

        var content = article.Content;
        if (string.IsNullOrWhiteSpace(content)) return;

        var tokens = CountTokens(content);

        if (tokens <= _chunkSize)
        {
            var fullText = $"{article.Path}\n---\n{content}";
            var meta = BuildChunkMeta(baseMeta, article.Path, "article");
            chunks.Add(new Chunk(fullText, chunks.Count, meta));
            return;
        }

        // Article too large — split by Khoản (numbered clauses: "1.", "2.", ...)
        var clauses = SplitByClauses(content);
        if (clauses.Count > 1)
        {
            foreach (var (clauseNum, clauseText) in clauses)
            {
                var clausePath = $"{article.Path} > Khoản {clauseNum}";
                if (CountTokens(clauseText) <= _chunkSize)
                {
                    var fullText = $"{clausePath}\n---\n{clauseText}";
                    var meta = BuildChunkMeta(baseMeta, clausePath, "article");
                    chunks.Add(new Chunk(fullText, chunks.Count, meta));
                }
                else
                {
                    var points = SplitByPoints(clauseText);
                    if (points.Count > 1)
                    {
                        foreach (var (pointLabel, pointText) in points)
                        {
                            var pointPath = $"{clausePath} > Điểm {pointLabel}";
                            EmitChunks(pointText, pointPath, "article", chunks, baseMeta);
                        }
                    }
                    else
                    {
                        EmitChunks(clauseText, clausePath, "article", chunks, baseMeta);
                    }
                }
            }
        }
        else
        {
            EmitChunks(content, article.Path, "article", chunks, baseMeta);
        }
    }

    private void EmitChunks(string content, string path, string sectionType,
        List<Chunk> chunks, Dictionary<string, string> baseMeta)
    {
        if (CountTokens(content) <= _chunkSize)
        {
            var fullText = $"{path}\n---\n{content}";
            var meta = BuildChunkMeta(baseMeta, path, sectionType);
            chunks.Add(new Chunk(fullText, chunks.Count, meta));
            return;
        }

        // Semantic split by sentences
        var sentences = SentenceSplitPattern.Split(content)
            .Select(s => s.Trim())
            .Where(s => !string.IsNullOrEmpty(s))
            .ToList();

        var currentText = "";
        foreach (var sentence in sentences)
        {
            var combined = string.IsNullOrEmpty(currentText) ? sentence : $"{currentText} {sentence}";
            if (CountTokens(combined) > _chunkSize && !string.IsNullOrEmpty(currentText))
            {
                var fullText = $"{path}\n---\n{currentText}";
                var meta = BuildChunkMeta(baseMeta, path, sectionType);
                chunks.Add(new Chunk(fullText, chunks.Count, meta));
                currentText = sentence;
            }
            else
            {
                currentText = combined;
            }
        }

        if (!string.IsNullOrEmpty(currentText))
        {
            var fullText = $"{path}\n---\n{currentText}";
            var meta = BuildChunkMeta(baseMeta, path, sectionType);
            chunks.Add(new Chunk(fullText, chunks.Count, meta));
        }
    }

    // ── Clause/Point splitting ─────────────────────────────────────────

    private static List<(string Number, string Text)> SplitByClauses(string content)
    {
        var parts = ClauseSplitRegex().Split(content);
        var results = new List<(string, string)>();

        foreach (var part in parts)
        {
            var trimmed = part.Trim();
            if (string.IsNullOrEmpty(trimmed)) continue;

            var match = Regex.Match(trimmed, @"^(\d+)\.\s*(.*)$", RegexOptions.Singleline);
            if (match.Success)
                results.Add((match.Groups[1].Value, match.Groups[2].Value.Trim()));
            else if (results.Count == 0)
                results.Add(("0", trimmed));
        }

        return results;
    }

    private static List<(string Label, string Text)> SplitByPoints(string content)
    {
        var parts = PointSplitRegex().Split(content);
        var results = new List<(string, string)>();

        foreach (var part in parts)
        {
            var trimmed = part.Trim();
            if (string.IsNullOrEmpty(trimmed)) continue;

            var match = Regex.Match(trimmed, @"^([a-zđ])\)\s*(.*)$", RegexOptions.Singleline);
            if (match.Success)
                results.Add((match.Groups[1].Value, match.Groups[2].Value.Trim()));
            else if (results.Count == 0)
                results.Add(("", trimmed));
        }

        return results;
    }

    // ── Metadata helpers ───────────────────────────────────────────────

    private Dictionary<string, string> BuildChunkMeta(
        Dictionary<string, string> baseMeta, string sectionPath, string sectionType)
    {
        var meta = new Dictionary<string, string>(baseMeta);
        if (!string.IsNullOrEmpty(sectionPath))
            meta["section"] = sectionPath;
        meta["section_type"] = sectionType;

        if (_metadata.DocumentType is not null)
            meta["document_type"] = _metadata.DocumentType;
        if (_metadata.DocumentNumber is not null)
            meta["document_number"] = _metadata.DocumentNumber;
        if (_metadata.IssuingAuthority is not null)
            meta["issuing_authority"] = _metadata.IssuingAuthority;
        if (_metadata.IssuedDate.HasValue)
            meta["issue_date"] = _metadata.IssuedDate.Value.ToString("yyyy-MM-dd");

        return meta;
    }

    private int CountTokens(string text) => _encoding.Encode(text).Count;
}
