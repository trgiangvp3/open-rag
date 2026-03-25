using System.Text.RegularExpressions;
using SharpToken;

namespace OpenRAG.Api.Services.Chunking;

public record Chunk(string Text, int Index, Dictionary<string, string> Metadata);

public class MarkdownChunker
{
    private readonly int _chunkSize;
    private readonly int _chunkOverlap;
    private readonly GptEncoding _encoding;

    // Vietnamese metadata sections to exclude (revision history, approval tables)
    private static readonly Regex[] MetadataPatterns =
    [
        new(@"\*?\*?PH[AÂ]N PH[OỐ]I\*?\*?", RegexOptions.IgnoreCase),
        new(@"\*?\*?S[UỬ]A [DĐ][OỔ]I\*?\*?", RegexOptions.IgnoreCase),
        new(@"\*?\*?THEO D[OÕ]I S[UỬ]A [DĐ][OỔ]I\*?\*?", RegexOptions.IgnoreCase),
        new(@"\*?\*?SO[AẠ]N TH[AẢ]O\*?\*?", RegexOptions.IgnoreCase),
        new(@"\*?\*?PH[EÊ] DUY[EỆ]T\*?\*?", RegexOptions.IgnoreCase),
    ];

    private static readonly Regex HeaderPattern = new(@"^(#{1,6})\s+(.+)$", RegexOptions.Multiline);
    private static readonly Regex BoldHeaderPattern = new(@"^\*\*(\d+[\.\)]\s*.+?)\*\*\s*$", RegexOptions.Multiline);
    private static readonly Regex TableRowPattern = new(@"^\|.+\|$", RegexOptions.Multiline);
    private static readonly Regex TableSepPattern = new(@"^\|[\s\-:]+\|$");
    private static readonly Regex SentenceSplitPattern = new(@"(?<=[.!?])\s+|\n+");

    public MarkdownChunker(int chunkSize = 150, int chunkOverlap = 20)
    {
        _chunkSize = chunkSize;
        _chunkOverlap = chunkOverlap;
        // Use cl100k_base encoding (same token space as tiktoken cl100k)
        _encoding = GptEncoding.GetEncoding("cl100k_base");
    }

    public List<Chunk> Chunk(string text, Dictionary<string, string>? metadata = null)
    {
        metadata ??= [];

        text = StripMetadataSections(text);
        var sections = SplitBySections(text);
        var chunks = new List<Chunk>();

        foreach (var section in sections)
        {
            var sectionChunks = SplitSectionSemantic(section.Text);
            foreach (var chunkText in sectionChunks)
            {
                var trimmed = chunkText.Trim();
                if (string.IsNullOrEmpty(trimmed) || trimmed.Length < 20) continue;

                var fullText = string.IsNullOrEmpty(section.Header)
                    ? trimmed
                    : $"{section.Header}\n\n{trimmed}";

                var chunkMeta = new Dictionary<string, string>(metadata);
                if (!string.IsNullOrEmpty(section.Header))
                    chunkMeta["section"] = section.Header;

                chunks.Add(new Chunk(fullText, chunks.Count, chunkMeta));
            }
        }

        return chunks;
    }

    // ── Metadata stripping ────────────────────────────────────────────────

    private static string StripMetadataSections(string text)
    {
        var lines = text.Split('\n');
        var result = new List<string>();
        var skipUntilBlank = false;
        var skipTable = false;

        foreach (var line in lines)
        {
            if (MetadataPatterns.Any(p => p.IsMatch(line)))
            {
                skipUntilBlank = true;
                skipTable = true;
                continue;
            }

            if (skipTable)
            {
                var stripped = line.Trim();
                if (TableRowPattern.IsMatch(stripped) || stripped.StartsWith("|") || stripped == "")
                {
                    if (stripped == "" && !TableRowPattern.IsMatch(stripped))
                    {
                        skipTable = false;
                        skipUntilBlank = false;
                    }
                    continue;
                }
                skipTable = false;
                skipUntilBlank = false;
            }

            if (!skipUntilBlank)
                result.Add(line);
        }

        return string.Join('\n', result);
    }

    // ── Section splitting ─────────────────────────────────────────────────

    private record SectionInfo(string Header, string Text);

    private static List<SectionInfo> SplitBySections(string text)
    {
        var headers = new List<(int Start, int End, string Header, int Level)>();

        foreach (Match m in HeaderPattern.Matches(text))
            headers.Add((m.Index, m.Index + m.Length, m.Groups[2].Value.Trim(), m.Groups[1].Length));

        foreach (Match m in BoldHeaderPattern.Matches(text))
            headers.Add((m.Index, m.Index + m.Length, m.Groups[1].Value.Trim(), 2));

        headers.Sort((a, b) => a.Start.CompareTo(b.Start));

        if (headers.Count == 0)
            return [new SectionInfo("", text)];

        var sections = new List<SectionInfo>();
        var stack = new List<(string Header, int Level)>();

        for (int i = 0; i < headers.Count; i++)
        {
            var (start, end, header, level) = headers[i];
            int contentEnd = i + 1 < headers.Count ? headers[i + 1].Start : text.Length;
            var content = text[end..contentEnd].Trim();

            while (stack.Count > 0 && stack[^1].Level >= level)
                stack.RemoveAt(stack.Count - 1);
            stack.Add((header, level));

            var path = string.Join(" > ", stack.Select(h => h.Header));

            if (!string.IsNullOrEmpty(content))
                sections.Add(new SectionInfo(path, content));
        }

        var preContent = text[..headers[0].Start].Trim();
        if (!string.IsNullOrEmpty(preContent))
            sections.Insert(0, new SectionInfo("", preContent));

        return sections;
    }

    // ── Semantic splitting within a section ──────────────────────────────

    private List<string> SplitSectionSemantic(string text)
    {
        var blocks = ExtractBlocks(text);
        var chunks = new List<string>();
        var current = "";

        foreach (var block in blocks)
        {
            var blockTokens = CountTokens(block);

            if (blockTokens > _chunkSize)
            {
                if (!string.IsNullOrWhiteSpace(current))
                {
                    chunks.Add(current.Trim());
                    current = "";
                }
                chunks.AddRange(SplitBySentences(block));
                continue;
            }

            var combined = string.IsNullOrEmpty(current) ? block : $"{current}\n\n{block}";
            if (CountTokens(combined) > _chunkSize && !string.IsNullOrWhiteSpace(current))
            {
                chunks.Add(current.Trim());
                var overlap = GetOverlapText(current);
                current = string.IsNullOrEmpty(overlap) ? block : $"{overlap}\n\n{block}";
            }
            else
            {
                current = combined.Trim();
            }
        }

        if (!string.IsNullOrWhiteSpace(current))
            chunks.Add(current.Trim());

        return chunks;
    }

    private static List<string> ExtractBlocks(string text)
    {
        var lines = text.Split('\n');
        var blocks = new List<string>();
        var current = new List<string>();
        var inTable = false;

        foreach (var line in lines)
        {
            var stripped = line.Trim();

            if (TableRowPattern.IsMatch(stripped) || (stripped.StartsWith("|") && stripped.EndsWith("|")))
            {
                if (!inTable && current.Count > 0)
                {
                    blocks.Add(string.Join('\n', current));
                    current.Clear();
                }
                inTable = true;
                current.Add(line);
                continue;
            }

            if (inTable && stripped == "")
            {
                blocks.Add(string.Join('\n', current));
                current.Clear();
                inTable = false;
                continue;
            }

            if (inTable && stripped != "")
            {
                if (TableSepPattern.IsMatch(stripped))
                {
                    current.Add(line);
                    continue;
                }
                blocks.Add(string.Join('\n', current));
                current.Clear();
                inTable = false;
            }

            if (stripped == "")
            {
                if (current.Count > 0)
                {
                    blocks.Add(string.Join('\n', current));
                    current.Clear();
                }
                continue;
            }

            current.Add(line);
        }

        if (current.Count > 0)
            blocks.Add(string.Join('\n', current));

        return blocks.Where(b => !string.IsNullOrWhiteSpace(b)).Select(b => b.Trim()).ToList();
    }

    private List<string> SplitBySentences(string text)
    {
        var sentences = SentenceSplitPattern.Split(text)
            .Select(s => s.Trim())
            .Where(s => !string.IsNullOrEmpty(s))
            .ToList();

        var chunks = new List<string>();
        var current = "";

        foreach (var sent in sentences)
        {
            var combined = string.IsNullOrEmpty(current) ? sent : $"{current} {sent}";
            if (CountTokens(combined) > _chunkSize && !string.IsNullOrEmpty(current))
            {
                chunks.Add(current);
                current = sent;
            }
            else
            {
                current = combined;
            }
        }

        if (!string.IsNullOrEmpty(current))
            chunks.Add(current);

        return chunks;
    }

    private string GetOverlapText(string text)
    {
        if (_chunkOverlap <= 0) return "";
        var words = text.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (words.Length <= _chunkOverlap) return text;
        return string.Join(' ', words[^_chunkOverlap..]);
    }

    private int CountTokens(string text) => _encoding.Encode(text).Count;
}
