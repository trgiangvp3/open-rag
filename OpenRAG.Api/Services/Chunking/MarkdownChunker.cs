using System.Text.RegularExpressions;
using Jint;
using SharpToken;

namespace OpenRAG.Api.Services.Chunking;

public record Chunk(string Text, int Index, Dictionary<string, string> Metadata);

public class MarkdownChunker
{
    private readonly int _chunkSize;
    private readonly int _chunkOverlap;
    private readonly int _sectionTokenThreshold;
    private readonly bool _autoDetectHeadings;
    private readonly string? _headingScript;
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
    private static readonly Regex StandaloneBoldLinePattern = new(
        @"^\*\*(?<text>[^\*\n]{3,200})\*\*\s*$", RegexOptions.Multiline);
    private static readonly Regex TableRowPattern = new(@"^\|.+\|$", RegexOptions.Multiline);
    private static readonly Regex TableSepPattern = new(@"^\|[\s\-:]+\|$");
    private static readonly Regex SentenceSplitPattern = new(@"(?<=[.!?;])\s+|\n+");

    public MarkdownChunker(
        int chunkSize = 400,
        int chunkOverlap = 50,
        int sectionTokenThreshold = 800,
        bool autoDetectHeadings = true,
        string? headingScript = null)
    {
        _chunkSize = chunkSize;
        _chunkOverlap = chunkOverlap;
        _sectionTokenThreshold = sectionTokenThreshold;
        _autoDetectHeadings = autoDetectHeadings;
        _headingScript = headingScript;
        // Use cl100k_base encoding (same token space as tiktoken cl100k)
        _encoding = GptEncoding.GetEncoding("cl100k_base");
    }

    public List<Chunk> Chunk(string text, Dictionary<string, string>? metadata = null)
    {
        metadata ??= [];

        text = StripMetadataSections(text);
        var sections = SplitBySections(text);
        sections = EnrichOversizedSections(sections);
        var chunks = new List<Chunk>();

        foreach (var section in sections)
        {
            var trimmedContent = section.Text.Trim();
            if (string.IsNullOrEmpty(trimmedContent) || trimmedContent.Length < 20) continue;

            var sectionTokens = CountTokens(trimmedContent);

            // Build metadata for this section
            var chunkMeta = new Dictionary<string, string>(metadata);
            if (!string.IsNullOrEmpty(section.Header))
                chunkMeta["section"] = section.Header;

            if (sectionTokens <= _chunkSize)
            {
                // Section fits in 1 chunk — keep as-is, never merge with other sections
                var fullText = string.IsNullOrEmpty(section.Header)
                    ? trimmedContent
                    : $"{section.Header}\n\n{trimmedContent}";
                chunks.Add(new Chunk(fullText, chunks.Count, new Dictionary<string, string>(chunkMeta)));
            }
            else
            {
                // Section too large — split semantically within the section
                var sectionChunks = SplitSectionSemantic(trimmedContent);
                foreach (var chunkText in sectionChunks)
                {
                    var trimmed = chunkText.Trim();
                    if (string.IsNullOrEmpty(trimmed) || trimmed.Length < 20) continue;

                    var fullText = string.IsNullOrEmpty(section.Header)
                        ? trimmed
                        : $"{section.Header}\n\n{trimmed}";
                    chunks.Add(new Chunk(fullText, chunks.Count, new Dictionary<string, string>(chunkMeta)));
                }
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

    // ── Enrich oversized sections with heading detection ──────────────────

    private List<SectionInfo> EnrichOversizedSections(List<SectionInfo> sections)
    {
        if (_sectionTokenThreshold <= 0) return sections;

        var enriched = new List<SectionInfo>();

        foreach (var section in sections)
        {
            if (CountTokens(section.Text) > _sectionTokenThreshold)
            {
                var subSections = DetectAndSplitHeadings(section);
                enriched.AddRange(subSections);
            }
            else
            {
                enriched.Add(section);
            }
        }

        return enriched;
    }

    private List<SectionInfo> DetectAndSplitHeadings(SectionInfo section)
    {
        var lines = section.Text.Split('\n');
        var detectedHeadings = new List<(int LineIndex, int Level, string Text)>();

        for (int i = 0; i < lines.Length; i++)
        {
            var line = lines[i];
            var heading = DetectHeadingInLine(line, i, lines);
            if (heading is not null)
                detectedHeadings.Add((i, heading.Value.Level, heading.Value.Text));
        }

        if (detectedHeadings.Count == 0)
            return [section];

        var subSections = new List<SectionInfo>();

        // Content before first detected heading
        if (detectedHeadings[0].LineIndex > 0)
        {
            var preLines = lines[..detectedHeadings[0].LineIndex];
            var preText = string.Join('\n', preLines).Trim();
            if (!string.IsNullOrEmpty(preText))
                subSections.Add(new SectionInfo(section.Header, preText));
        }

        for (int i = 0; i < detectedHeadings.Count; i++)
        {
            var (lineIdx, level, headingText) = detectedHeadings[i];
            int startLine = lineIdx + 1;
            int endLine = i + 1 < detectedHeadings.Count
                ? detectedHeadings[i + 1].LineIndex
                : lines.Length;

            if (startLine >= endLine) continue;

            var contentLines = lines[startLine..endLine];
            var content = string.Join('\n', contentLines).Trim();
            if (string.IsNullOrEmpty(content)) continue;

            var headerPath = string.IsNullOrEmpty(section.Header)
                ? headingText
                : $"{section.Header} > {headingText}";

            subSections.Add(new SectionInfo(headerPath, content));
        }

        return subSections.Count > 0 ? subSections : [section];
    }

    private (int Level, string Text)? DetectHeadingInLine(string line, int index, string[] allLines)
    {
        // 1. Custom JS heading script (Jint)
        if (!string.IsNullOrWhiteSpace(_headingScript))
        {
            var result = RunHeadingScript(line, index, allLines);
            if (result is not null)
                return result;
        }

        // 2. Auto-detect patterns
        if (_autoDetectHeadings)
        {
            var trimmed = line.Trim();

            // Standalone bold line: **Some Heading Text**
            var boldMatch = StandaloneBoldLinePattern.Match(line);
            if (boldMatch.Success)
                return (3, boldMatch.Groups["text"].Value.Trim());

            // ALL-CAPS line (at least 3 chars, mostly uppercase letters/digits/spaces, not a table row)
            if (trimmed.Length >= 3
                && !trimmed.StartsWith("|")
                && !trimmed.StartsWith("#")
                && IsAllCapsHeading(trimmed))
                return (3, trimmed);
        }

        return null;
    }

    private static bool IsAllCapsHeading(string text)
    {
        // Must contain at least 2 letter characters
        int letterCount = 0;
        int upperCount = 0;

        foreach (var c in text)
        {
            if (char.IsLetter(c))
            {
                letterCount++;
                if (char.IsUpper(c)) upperCount++;
            }
        }

        // Must have at least 2 letters and >80% uppercase
        return letterCount >= 2 && upperCount >= letterCount * 0.8;
    }

    private (int Level, string Text)? RunHeadingScript(string line, int index, string[] allLines)
    {
        try
        {
            var engine = new Engine(options => options
                .LimitRecursion(64)
                .TimeoutInterval(TimeSpan.FromMilliseconds(200))
                .MaxStatements(1000));

            engine.Execute(_headingScript!);

            var result = engine.Invoke("detectHeading", line, index, allLines);

            if (result.IsNull() || result.IsUndefined())
                return null;

            var obj = result.AsObject();
            var level = (int)obj.Get("level").AsNumber();
            var text = obj.Get("text").AsString();

            if (!string.IsNullOrWhiteSpace(text) && level > 0)
                return (level, text);
        }
        catch
        {
            // Script errors are silently ignored for robustness
        }

        return null;
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
            // If a single sentence exceeds chunk size, split it further
            if (CountTokens(sent) > _chunkSize)
            {
                if (!string.IsNullOrEmpty(current))
                {
                    chunks.Add(current);
                    current = "";
                }
                chunks.AddRange(SplitByCommaOrWords(sent));
                continue;
            }

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

    private List<string> SplitByCommaOrWords(string text)
    {
        // Try splitting by commas first
        var parts = text.Split(',')
            .Select(p => p.Trim())
            .Where(p => !string.IsNullOrEmpty(p))
            .ToList();

        if (parts.Count > 1)
        {
            var chunks = new List<string>();
            var current = "";

            foreach (var part in parts)
            {
                var combined = string.IsNullOrEmpty(current) ? part : $"{current}, {part}";
                if (CountTokens(combined) > _chunkSize && !string.IsNullOrEmpty(current))
                {
                    chunks.Add(current);
                    current = part;
                }
                else
                {
                    current = combined;
                }
            }

            if (!string.IsNullOrEmpty(current))
                chunks.Add(current);

            // Check if all chunks fit; if any still too large, fall through to word splitting
            if (chunks.All(c => CountTokens(c) <= _chunkSize))
                return chunks;
        }

        // Fall back to word splitting
        return SplitByWords(text);
    }

    private List<string> SplitByWords(string text)
    {
        var words = text.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var chunks = new List<string>();
        var current = "";

        foreach (var word in words)
        {
            var combined = string.IsNullOrEmpty(current) ? word : $"{current} {word}";
            if (CountTokens(combined) > _chunkSize && !string.IsNullOrEmpty(current))
            {
                chunks.Add(current);
                current = word;
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
