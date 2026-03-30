using System.Net;
using System.Text.RegularExpressions;
using HtmlAgilityPack;

namespace OpenRAG.Api.Services.Parsing;

/// <summary>
/// Parses Vietnamese legal documents (VBQPPL) from TVPL HTML format.
/// Relies on TVPL anchor naming conventions (loai_1, dieu_X, chuong_X, etc.).
/// </summary>
public static partial class LegalHtmlParser
{
    private static readonly Dictionary<string, (string Key, string Display)> DocumentTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        ["LUẬT"] = ("luat", "Luật"),
        ["NGHỊ ĐỊNH"] = ("nghi_dinh", "Nghị định"),
        ["THÔNG TƯ"] = ("thong_tu", "Thông tư"),
        ["QUYẾT ĐỊNH"] = ("quyet_dinh", "Quyết định"),
        ["NGHỊ QUYẾT"] = ("nghi_quyet", "Nghị quyết"),
        ["CHỈ THỊ"] = ("chi_thi", "Chỉ thị"),
        ["CÔNG VĂN"] = ("cong_van", "Công văn"),
        ["THÔNG TƯ LIÊN TỊCH"] = ("thong_tu_lien_tich", "Thông tư liên tịch"),
    };

    /// <summary>TVPL URL path segment → Vietnamese display tag.</summary>
    private static readonly Dictionary<string, string> TvplCategoryTags = new(StringComparer.OrdinalIgnoreCase)
    {
        ["Tien-te-Ngan-hang"] = "Tiền tệ - Ngân hàng",
        ["Tai-chinh-nha-nuoc"] = "Tài chính nhà nước",
        ["Bo-may-hanh-chinh"] = "Bộ máy hành chính",
        ["Doanh-nghiep"] = "Doanh nghiệp",
        ["Dau-tu"] = "Đầu tư",
        ["Chung-khoan"] = "Chứng khoán",
        ["Bat-dong-san"] = "Bất động sản",
        ["Thue-Phi-Le-Phi"] = "Thuế - Phí - Lệ phí",
        ["Thuong-mai"] = "Thương mại",
        ["Ke-toan-Kiem-toan"] = "Kế toán - Kiểm toán",
        ["Cong-nghe-thong-tin"] = "Công nghệ thông tin",
        ["Lao-dong-Tien-luong"] = "Lao động - Tiền lương",
        ["Tai-nguyen-Moi-truong"] = "Tài nguyên - Môi trường",
        ["Quyen-dan-su"] = "Quyền dân sự",
        ["Giao-duc"] = "Giáo dục",
        ["Y-te"] = "Y tế",
        ["Giao-thong-Van-tai"] = "Giao thông - Vận tải",
        ["Xay-dung-Do-thi"] = "Xây dựng - Đô thị",
        ["Bao-hiem"] = "Bảo hiểm",
        ["So-huu-tri-tue"] = "Sở hữu trí tuệ",
        ["Cong-nghe-thong-tin"] = "Công nghệ thông tin",
        ["An-ninh-quoc-gia"] = "An ninh quốc gia",
        ["Quoc-phong"] = "Quốc phòng",
        ["Nong-nghiep"] = "Nông nghiệp",
        ["Van-hoa-Xa-hoi"] = "Văn hóa - Xã hội",
        ["Linh-vuc-khac"] = "Lĩnh vực khác",
        ["Xuat-nhap-khau"] = "Xuất nhập khẩu",
        ["Trach-nhiem-hinh-su"] = "Trách nhiệm hình sự",
        ["Thu-tuc-To-tung"] = "Thủ tục tố tụng",
        ["Vi-pham-hanh-chinh"] = "Vi phạm hành chính",
        ["Quyen-dan-su"] = "Quyền dân sự",
    };

    [GeneratedRegex(@"thuvienphapluat\.vn/van-ban/([^/""]+)/", RegexOptions.IgnoreCase)]
    private static partial Regex TvplCategoryRegex();

    [GeneratedRegex(@"Số[:\s]*(\d+/\d{4}/[A-ZĐa-zđ\-]+)", RegexOptions.IgnoreCase)]
    private static partial Regex DocumentNumberRegex();

    [GeneratedRegex(@"Điều\s+(\d+)[\.\:]?\s*(.*)", RegexOptions.IgnoreCase)]
    private static partial Regex ArticleBoldRegex();

    [GeneratedRegex(@"Chương\s+([IVXLCDM\d]+)[\.\:]?\s*(.*)", RegexOptions.IgnoreCase)]
    private static partial Regex ChapterBoldRegex();

    [GeneratedRegex(@"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", RegexOptions.IgnoreCase)]
    private static partial Regex DateRegex();

    [GeneratedRegex(@"^(\d+)\.\s*(.+?)(?:\s+là\s+|\s+được hiểu là\s+|\s+bao\s*gồm\s+|\s+có nghĩa là\s+)(.+)", RegexOptions.Singleline)]
    private static partial Regex TermDefinitionRegex();

    [GeneratedRegex(@"^(\d+)\.\s*", RegexOptions.Multiline)]
    private static partial Regex NumberedItemRegex();

    /// <summary>
    /// Quick check whether an HTML string looks like a TVPL legal document.
    /// </summary>
    public static bool IsLegalHtml(string html)
    {
        // New format: TVPL anchors
        if (html.Contains("name=\"loai_1\"", StringComparison.OrdinalIgnoreCase))
            return true;
        // Old format: check fragments that survive whitespace/newline variations
        return html.Contains("CỘNG HÒA XÃ HỘI", StringComparison.OrdinalIgnoreCase)
            && html.Contains("NGHĨA VIỆT NAM", StringComparison.OrdinalIgnoreCase)
            && html.Contains("Căn cứ", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// Parse a TVPL legal document HTML and extract all metadata + structure.
    /// Returns null if the document is not a recognized legal document format.
    /// </summary>
    public static LegalDocumentMetadata? TryParse(string html)
    {
        if (!IsLegalHtml(html)) return null;

        var doc = new HtmlDocument();
        doc.LoadHtml(html);

        var docType = ExtractDocumentType(doc);
        if (docType is null) return null;

        var (typeKey, typeDisplay) = docType.Value;
        var docNumber = ExtractDocumentNumber(doc);
        var docTitle = ExtractDocumentTitle(doc);
        var (authority, location) = ExtractIssuingAuthority(doc);
        var issuedDate = ExtractIssuedDate(doc);
        var legalBases = ExtractLegalBases(doc);
        var referencedDocs = ExtractReferencedDocs(doc);
        var sections = ExtractSections(doc);
        var terminology = ExtractTerminology(doc, sections);
        var plainText = ExtractPlainText(doc);
        var subjects = ExtractSubjects(sections);
        var suggestedDomains = SuggestDomains(docTitle, authority, doc);

        return new LegalDocumentMetadata(
            DocumentType: typeKey,
            DocumentTypeDisplay: typeDisplay,
            DocumentNumber: docNumber,
            DocumentTitle: docTitle,
            IssuingAuthority: authority,
            SignedLocation: location,
            IssuedDate: issuedDate,
            LegalBases: legalBases,
            Terminology: terminology,
            ReferencedDocs: referencedDocs,
            Sections: sections,
            PlainText: plainText,
            Subjects: subjects,
            SuggestedDomains: suggestedDomains);
    }

    // ── Document type ──────────────────────────────────────────────────

    private static (string Key, string Display)? ExtractDocumentType(HtmlDocument doc)
    {
        // Try anchor-based detection first (new TVPL format)
        var anchor = doc.DocumentNode.SelectSingleNode("//a[@name='loai_1']");
        if (anchor is not null)
        {
            var text = CollapseWhitespace(NormalizeText(anchor.InnerText)).ToUpperInvariant();
            foreach (var (keyword, value) in DocumentTypes.OrderByDescending(kv => kv.Key.Length))
                if (text.Contains(keyword, StringComparison.OrdinalIgnoreCase))
                    return value;
        }

        // Fallback: scan centered bold text near top of document (old format)
        var candidates = doc.DocumentNode.SelectNodes(
            "//p[@align='center']//b | //p[contains(@style,'text-align:center')]//b");
        if (candidates is not null)
        {
            foreach (var node in candidates)
            {
                if (node.StreamPosition > 4000) break;
                var text = CollapseWhitespace(NormalizeText(node.InnerText)).ToUpperInvariant();
                if (text.Length > 50) continue;

                foreach (var (keyword, value) in DocumentTypes.OrderByDescending(kv => kv.Key.Length))
                    if (text.Contains(keyword, StringComparison.OrdinalIgnoreCase))
                        return value;
            }
        }

        return null;
    }

    // ── Document title ─────────────────────────────────────────────────

    private static string? ExtractDocumentTitle(HtmlDocument doc)
    {
        // New format: dedicated anchor
        var anchor = doc.DocumentNode.SelectSingleNode("//a[@name='loai_1_name']");
        if (anchor is not null)
        {
            var text = NormalizeText(anchor.InnerText).Trim();
            if (!string.IsNullOrEmpty(text)) return text;
        }

        // Old format: centered paragraph right after the document type line
        var candidates = doc.DocumentNode.SelectNodes(
            "//p[@align='center'] | //p[contains(@style,'text-align:center')]");
        if (candidates is null) return null;

        var foundType = false;
        foreach (var p in candidates)
        {
            if (p.StreamPosition > 5000) break;
            var text = NormalizeText(p.InnerText).Trim();
            var upper = text.ToUpperInvariant();

            if (!foundType)
            {
                // Look for the type line (THÔNG TƯ, NGHỊ ĐỊNH, etc.)
                if (DocumentTypes.Keys.Any(k => upper.Contains(k)))
                    foundType = true;
                continue;
            }

            // Next centered paragraph after type = title
            if (text.Length > 5 && !text.StartsWith("Căn cứ", StringComparison.OrdinalIgnoreCase))
                return text;
        }

        return null;
    }

    // ── Document number ────────────────────────────────────────────────

    private static string? ExtractDocumentNumber(HtmlDocument doc)
    {
        // Look in the header table, first row, first cell for "Số: ..."
        var tables = doc.DocumentNode.SelectNodes("//table");
        if (tables is null) return null;

        var headerTable = tables[0]; // First table is the header
        var cells = headerTable.SelectNodes(".//td");
        if (cells is null || cells.Count < 2) return null;

        // Second row, first cell typically has the number
        var rows = headerTable.SelectNodes(".//tr");
        if (rows is null || rows.Count < 2) return null;

        var secondRowCells = rows[1].SelectNodes(".//td");
        if (secondRowCells is null || secondRowCells.Count == 0) return null;

        var cellText = CollapseWhitespace(NormalizeText(secondRowCells[0].InnerText));
        var match = DocumentNumberRegex().Match(cellText);
        return match.Success ? match.Groups[1].Value.Trim() : null;
    }

    // ── Issuing authority ──────────────────────────────────────────────

    private static (string? Authority, string? Location) ExtractIssuingAuthority(HtmlDocument doc)
    {
        var tables = doc.DocumentNode.SelectNodes("//table");
        if (tables is null) return (null, null);

        var headerTable = tables[0];
        var rows = headerTable.SelectNodes(".//tr");
        if (rows is null || rows.Count == 0) return (null, null);

        // First row, first cell = issuing authority
        var firstRowCells = rows[0].SelectNodes(".//td");
        if (firstRowCells is null || firstRowCells.Count < 2) return (null, null);

        var authorityText = NormalizeText(firstRowCells[0].InnerText)
            .Replace("-------", "").Replace("---", "").Trim();
        // Remove trailing dashes
        authorityText = authorityText.TrimEnd('-', ' ', '\n', '\r');
        if (string.IsNullOrEmpty(authorityText)) authorityText = null;

        // Second row, second cell has location + date
        string? location = null;
        if (rows.Count >= 2)
        {
            var secondRowCells = rows[1].SelectNodes(".//td");
            if (secondRowCells is not null && secondRowCells.Count >= 2)
            {
                var dateText = NormalizeText(secondRowCells[1].InnerText).Trim();
                // Extract location before "ngày" — e.g., "Hà Nội, ngày 31 tháng 12 năm 2025"
                var commaIdx = dateText.IndexOf(',');
                if (commaIdx > 0)
                    location = dateText[..commaIdx].Trim();
            }
        }

        return (authorityText, location);
    }

    // ── Issue date ─────────────────────────────────────────────────────

    private static DateTime? ExtractIssuedDate(HtmlDocument doc)
    {
        var tables = doc.DocumentNode.SelectNodes("//table");
        if (tables is null) return null;

        var headerTable = tables[0];
        var rows = headerTable.SelectNodes(".//tr");
        if (rows is null || rows.Count < 2) return null;

        var secondRowCells = rows[1].SelectNodes(".//td");
        if (secondRowCells is null || secondRowCells.Count < 2) return null;

        var dateText = NormalizeText(secondRowCells[1].InnerText);
        return ParseVietnameseDate(dateText);
    }

    private static DateTime? ParseVietnameseDate(string text)
    {
        var match = DateRegex().Match(text);
        if (!match.Success) return null;

        if (int.TryParse(match.Groups[1].Value, out var day) &&
            int.TryParse(match.Groups[2].Value, out var month) &&
            int.TryParse(match.Groups[3].Value, out var year))
        {
            try { return new DateTime(year, month, day, 0, 0, 0, DateTimeKind.Utc); }
            catch { return null; }
        }

        return null;
    }

    // ── Legal bases (Căn cứ) ───────────────────────────────────────────

    private static List<LegalBasis> ExtractLegalBases(HtmlDocument doc)
    {
        var results = new List<LegalBasis>();

        // Find the first Điều to know where căn cứ section ends
        var firstArticle = doc.DocumentNode.SelectSingleNode("//a[@name='dieu_1']");
        // Fallback: find first bold "Điều 1" text
        if (firstArticle is null)
        {
            var bolds = doc.DocumentNode.SelectNodes("//b");
            if (bolds is not null)
                firstArticle = bolds.FirstOrDefault(b =>
                    NormalizeText(b.InnerText).TrimStart().StartsWith("Điều 1", StringComparison.OrdinalIgnoreCase));
        }
        if (firstArticle is null) return results;

        var allParagraphs = doc.DocumentNode.SelectNodes("//p") ?? Enumerable.Empty<HtmlNode>();

        foreach (var p in allParagraphs)
        {
            if (IsAfterNode(p, firstArticle)) break;

            var text = NormalizeText(p.InnerText).Trim();
            if (!text.StartsWith("Căn cứ", StringComparison.OrdinalIgnoreCase)) continue;

            // Extract linked references
            string? url = null;
            string? number = null;
            var links = p.SelectNodes(".//a[@href]");
            if (links is not null)
            {
                foreach (var link in links)
                {
                    var href = link.GetAttributeValue("href", "");
                    if (href.Contains("thuvienphapluat.vn", StringComparison.OrdinalIgnoreCase))
                    {
                        url ??= href;
                        var linkText = NormalizeText(link.InnerText).Trim();
                        // Try to extract document number from link text
                        var numMatch = DocumentNumberRegex().Match(linkText);
                        if (numMatch.Success) number ??= numMatch.Groups[1].Value;
                    }
                }
            }

            results.Add(new LegalBasis(text, number, url));
        }

        return results;
    }

    // ── Referenced documents (all tvpllink hrefs) ──────────────────────

    private static List<ReferencedDoc> ExtractReferencedDocs(HtmlDocument doc)
    {
        var results = new List<ReferencedDoc>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        var links = doc.DocumentNode.SelectNodes("//a[@href]");
        if (links is null) return results;

        foreach (var link in links)
        {
            var href = link.GetAttributeValue("href", "");
            if (!href.Contains("thuvienphapluat.vn", StringComparison.OrdinalIgnoreCase)) continue;
            if (!seen.Add(href)) continue;

            var linkText = NormalizeText(link.InnerText).Trim();
            var numMatch = DocumentNumberRegex().Match(linkText);
            var number = numMatch.Success ? numMatch.Groups[1].Value : linkText;

            results.Add(new ReferencedDoc(number, linkText, href));
        }

        return results;
    }

    // ── Subjects (Đối tượng áp dụng — from Điều 2) ──────────────────

    private static List<string> ExtractSubjects(List<LegalSection> sections)
    {
        var article = FindArticle(sections, s =>
            s.Title.Contains("Đối tượng áp dụng", StringComparison.OrdinalIgnoreCase));
        if (article is null) return [];

        var results = new List<string>();
        var items = NumberedItemRegex().Split(article.Content);

        for (int i = 1; i < items.Length; i += 2)
        {
            if (i + 1 >= items.Length) break;
            var text = items[i + 1].Trim().TrimEnd('.', ';');
            // Take the main subject description (first sentence or clause)
            var dotIdx = text.IndexOf('.');
            var subject = dotIdx > 0 && dotIdx < 200 ? text[..dotIdx].Trim() : text;
            if (subject.Length > 5 && subject.Length < 300)
                results.Add(subject);
        }

        return results;
    }

    // ── Domain suggestions (from authority + title keywords) ────────

    private static readonly Dictionary<string, (string Slug, string Name)> AuthorityDomainMap = new(StringComparer.OrdinalIgnoreCase)
    {
        ["NGÂN HÀNG NHÀ NƯỚC"] = ("ngan-hang-tin-dung", "Ngân hàng - Tín dụng"),
        ["BỘ TÀI CHÍNH"] = ("tai-chinh", "Tài chính"),
        ["BỘ THÔNG TIN VÀ TRUYỀN THÔNG"] = ("cong-nghe-thong-tin", "Công nghệ thông tin"),
        ["BỘ KẾ HOẠCH VÀ ĐẦU TƯ"] = ("dau-tu", "Đầu tư"),
        ["BỘ CÔNG THƯƠNG"] = ("doanh-nghiep", "Doanh nghiệp"),
    };

    private static readonly (string[] Keywords, string Slug, string Name)[] TitleDomainRules =
    [
        (["ngoại hối", "ngoại tệ", "tỷ giá"], "quan-ly-ngoai-hoi", "Quản lý ngoại hối"),
        (["thanh toán", "chuyển tiền"], "thanh-toan", "Thanh toán"),
        (["mạng lưới", "chi nhánh", "phòng giao dịch"], "mang-luoi-hoat-dong", "Mạng lưới hoạt động"),
        (["xếp hạng", "giám sát"], "xep-hang-giam-sat", "Xếp hạng - Giám sát"),
        (["phân loại nợ", "trích lập dự phòng", "dự phòng rủi ro"], "phan-loai-no", "Phân loại nợ - Trích lập dự phòng"),
        (["cho vay", "tín dụng", "cấp tín dụng"], "cho-vay", "Cho vay"),
        (["cấp phép", "thành lập ngân hàng", "giấy phép"], "cap-phep", "Cấp phép"),
        (["thuế", "phí", "lệ phí"], "thue-phi-le-phi", "Thuế - Phí - Lệ phí"),
        (["kế toán", "kiểm toán"], "ke-toan-kiem-toan", "Kế toán - Kiểm toán"),
        (["ngân sách"], "ngan-sach-nha-nuoc", "Ngân sách nhà nước"),
        (["an toàn thông tin", "an ninh mạng", "bảo mật"], "an-toan-thong-tin", "An toàn thông tin"),
        (["giao dịch điện tử", "chữ ký số"], "giao-dich-dien-tu", "Giao dịch điện tử"),
        (["đầu tư nước ngoài", "FDI"], "dau-tu-nuoc-ngoai", "Đầu tư nước ngoài"),
        (["chứng khoán", "cổ phiếu", "trái phiếu"], "chung-khoan", "Chứng khoán"),
        (["thành lập doanh nghiệp", "đăng ký doanh nghiệp"], "thanh-lap-dang-ky", "Thành lập - Đăng ký"),
    ];

    private static List<DomainSuggestion> SuggestDomains(string? title, string? authority, HtmlDocument doc)
    {
        var suggestions = new List<DomainSuggestion>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        // 1. From issuing authority → level 1 domain (high confidence)
        if (authority is not null)
        {
            var authorityUpper = authority.ToUpperInvariant();
            foreach (var (key, (slug, name)) in AuthorityDomainMap)
            {
                if (authorityUpper.Contains(key))
                {
                    suggestions.Add(new DomainSuggestion(name, slug, "authority", 0.9f));
                    seen.Add(slug);
                    break;
                }
            }
        }

        // 2. From document title → level 2 domain (medium-high confidence)
        if (title is not null)
        {
            var titleLower = title.ToLowerInvariant();
            foreach (var (keywords, slug, name) in TitleDomainRules)
            {
                if (seen.Contains(slug)) continue;
                foreach (var kw in keywords)
                {
                    if (titleLower.Contains(kw))
                    {
                        suggestions.Add(new DomainSuggestion(name, slug, "title", 0.8f));
                        seen.Add(slug);
                        break;
                    }
                }
            }
        }

        // 3. From TVPL URL categories → fallback (lower confidence)
        var links = doc.DocumentNode.SelectNodes("//a[@href]");
        if (links is not null)
        {
            foreach (var link in links)
            {
                var href = link.GetAttributeValue("href", "");
                var match = TvplCategoryRegex().Match(href);
                if (!match.Success) continue;
                var slug = match.Groups[1].Value;
                if (TvplCategoryTags.TryGetValue(slug, out var tag) && !seen.Contains(slug))
                {
                    suggestions.Add(new DomainSuggestion(tag, slug, "tvpl_url", 0.5f));
                    seen.Add(slug);
                }
            }
        }

        return suggestions.OrderByDescending(s => s.Confidence).ToList();
    }

    // ── Sections (Chương / Mục / Điều / Phụ lục) ──────────────────────

    private static List<LegalSection> ExtractSections(HtmlDocument doc)
    {
        var sections = new List<LegalSection>();

        // Collect structural anchors in document order (new TVPL format)
        var structuralNodes = new List<(HtmlNode Anchor, string Type, string Number)>();

        var anchors = doc.DocumentNode.SelectNodes("//a[@name]");
        if (anchors is not null)
        {
            foreach (var a in anchors)
            {
                var name = a.GetAttributeValue("name", "");
                if (TryParseAnchorName(name, out var type, out var number))
                    structuralNodes.Add((a, type, number));
            }
        }

        // Fallback: scan bold text for "Điều X", "Chương X" patterns (old format)
        if (structuralNodes.Count == 0)
            return ExtractSectionsByBoldText(doc);

        // Build tree: Chapters contain Sections contain Articles
        var chapters = new List<LegalSection>();
        LegalSection? currentChapter = null;
        LegalSection? currentMuc = null;

        for (int i = 0; i < structuralNodes.Count; i++)
        {
            var (anchor, type, number) = structuralNodes[i];
            var titleAnchor = FindTitleAnchor(doc, anchor, type, number);
            var title = titleAnchor is not null
                ? NormalizeText(titleAnchor.InnerText).Trim()
                : NormalizeText(anchor.InnerText).Trim();

            // Determine content range: from this anchor to the next structural anchor
            var contentStart = (titleAnchor ?? anchor).ParentNode ?? anchor;
            HtmlNode? contentEnd = i + 1 < structuralNodes.Count
                ? structuralNodes[i + 1].Anchor.ParentNode ?? structuralNodes[i + 1].Anchor
                : null;

            var content = ExtractContentBetween(doc, contentStart, contentEnd);

            switch (type)
            {
                case "chapter":
                case "appendix":
                {
                    currentMuc = null;
                    var chapterTitle = title;
                    // Combine "Chương I" with its name
                    if (titleAnchor is not null && titleAnchor != anchor)
                    {
                        var chapterLabel = NormalizeText(anchor.InnerText).Trim();
                        chapterTitle = $"{chapterLabel}: {title}";
                    }

                    currentChapter = new LegalSection(
                        Type: type,
                        Number: number,
                        Title: chapterTitle,
                        Content: content,
                        Path: chapterTitle,
                        Children: []);
                    chapters.Add(currentChapter);
                    break;
                }
                case "section":
                {
                    currentMuc = new LegalSection(
                        Type: "section",
                        Number: number,
                        Title: title,
                        Content: content,
                        Path: currentChapter is not null
                            ? $"{currentChapter.Path} > {title}"
                            : title,
                        Children: []);

                    if (currentChapter is not null)
                        currentChapter.Children.Add(currentMuc);
                    else
                        chapters.Add(currentMuc);
                    break;
                }
                case "article":
                {
                    var articleTitle = title;
                    var path = currentMuc?.Path ?? currentChapter?.Path ?? "";
                    path = string.IsNullOrEmpty(path) ? articleTitle : $"{path} > {articleTitle}";

                    var article = new LegalSection(
                        Type: "article",
                        Number: number,
                        Title: articleTitle,
                        Content: content,
                        Path: path,
                        Children: []);

                    if (currentMuc is not null)
                        currentMuc.Children.Add(article);
                    else if (currentChapter is not null)
                        currentChapter.Children.Add(article);
                    else
                        chapters.Add(article);
                    break;
                }
            }
        }

        return chapters;
    }

    /// <summary>Fallback section extraction by scanning bold text patterns (old TVPL format).</summary>
    private static List<LegalSection> ExtractSectionsByBoldText(HtmlDocument doc)
    {
        var chapters = new List<LegalSection>();
        LegalSection? currentChapter = null;

        // Find all bold elements that could be structural headings
        var boldNodes = doc.DocumentNode.SelectNodes("//p//b | //p/b");
        if (boldNodes is null) return chapters;

        var structuralElements = new List<(HtmlNode Node, string Type, string Number, string Title)>();

        foreach (var b in boldNodes)
        {
            var text = NormalizeText(b.InnerText).Trim();
            if (string.IsNullOrEmpty(text)) continue;

            var chapterMatch = ChapterBoldRegex().Match(text);
            if (chapterMatch.Success)
            {
                structuralElements.Add((b, "chapter", chapterMatch.Groups[1].Value, text));
                continue;
            }

            var articleMatch = ArticleBoldRegex().Match(text);
            if (articleMatch.Success)
            {
                structuralElements.Add((b, "article", articleMatch.Groups[1].Value, text));
            }
        }

        for (int i = 0; i < structuralElements.Count; i++)
        {
            var (node, type, number, title) = structuralElements[i];
            var parentP = node.ParentNode;

            // Extract content between this element and the next
            HtmlNode? endNode = i + 1 < structuralElements.Count
                ? structuralElements[i + 1].Node.ParentNode
                : null;

            var content = parentP is not null
                ? ExtractContentBetween(doc, parentP, endNode)
                : "";

            if (type == "chapter")
            {
                currentChapter = new LegalSection(
                    Type: "chapter", Number: number, Title: title,
                    Content: content, Path: title, Children: []);
                chapters.Add(currentChapter);
            }
            else if (type == "article")
            {
                var path = currentChapter is not null
                    ? $"{currentChapter.Path} > {title}" : title;

                var article = new LegalSection(
                    Type: "article", Number: number, Title: title,
                    Content: content, Path: path, Children: []);

                if (currentChapter is not null)
                    currentChapter.Children.Add(article);
                else
                    chapters.Add(article);
            }
        }

        return chapters;
    }

    private static bool TryParseAnchorName(string name, out string type, out string number)
    {
        type = "";
        number = "";

        if (name.StartsWith("chuong_pl_") && !name.EndsWith("_name"))
        {
            type = "appendix";
            number = name["chuong_pl_".Length..];
            return true;
        }

        if (name.StartsWith("chuong_") && !name.EndsWith("_name"))
        {
            type = "chapter";
            number = name["chuong_".Length..];
            return true;
        }

        if (name.StartsWith("muc_") && !name.EndsWith("_name"))
        {
            type = "section";
            number = name["muc_".Length..];
            return true;
        }

        if (name.StartsWith("dieu_"))
        {
            type = "article";
            number = name["dieu_".Length..];
            return true;
        }

        return false;
    }

    private static HtmlNode? FindTitleAnchor(HtmlDocument doc, HtmlNode anchor, string type, string number)
    {
        // TVPL convention: chuong_X has a sibling chuong_X_name, etc.
        var baseName = anchor.GetAttributeValue("name", "");
        var titleName = type switch
        {
            "chapter" => $"chuong_{number}_name",
            "appendix" => $"chuong_pl_{number}_name",
            "section" => null, // Mục title is inline
            "article" => null, // Điều title is inline
            _ => null,
        };

        if (titleName is null) return null;
        return doc.DocumentNode.SelectSingleNode($"//a[@name='{titleName}']");
    }

    private static string ExtractContentBetween(HtmlDocument doc, HtmlNode startNode, HtmlNode? endNode)
    {
        var parts = new List<string>();
        var current = startNode.NextSibling;
        // Also handle: startNode's parent's next sibling
        if (current is null)
            current = startNode.ParentNode?.NextSibling;

        while (current is not null)
        {
            if (endNode is not null && (current == endNode || IsAfterOrEqual(current, endNode)))
                break;

            // Check if this node contains an endNode descendant
            if (endNode is not null && current.Contains(endNode))
                break;

            var text = NormalizeText(current.InnerText).Trim();
            if (!string.IsNullOrEmpty(text) && text != "\u00a0") // skip &nbsp;
                parts.Add(text);

            current = current.NextSibling;
        }

        return string.Join("\n", parts);
    }

    // ── Terminology (Giải thích từ ngữ) ────────────────────────────────

    private static List<TermDefinition> ExtractTerminology(HtmlDocument doc, List<LegalSection> sections)
    {
        var results = new List<TermDefinition>();

        // Find article with "Giải thích từ ngữ" in title
        var termArticle = FindArticle(sections, s =>
            s.Title.Contains("Giải thích từ ngữ", StringComparison.OrdinalIgnoreCase) ||
            s.Title.Contains("giải thích từ ngữ", StringComparison.OrdinalIgnoreCase));

        if (termArticle is null) return results;

        // Parse numbered definitions from the article content
        var content = termArticle.Content;
        var items = NumberedItemRegex().Split(content);

        // items[0] is pre-content, then alternating: number, text
        for (int i = 1; i < items.Length; i += 2)
        {
            if (i + 1 >= items.Length) break;
            var itemText = items[i + 1].Trim();
            if (string.IsNullOrEmpty(itemText)) continue;

            // Try to split term and definition
            var match = TermDefinitionRegex().Match($"{items[i]}. {itemText}");
            if (match.Success)
            {
                var term = match.Groups[2].Value.Trim().TrimEnd('.');
                var definition = match.Groups[3].Value.Trim().TrimEnd(';', '.');
                results.Add(new TermDefinition(term, definition));
            }
            else
            {
                // Fallback: first sentence-ish chunk as term
                var firstDot = itemText.IndexOf('.');
                if (firstDot > 0 && firstDot < 200)
                {
                    results.Add(new TermDefinition(
                        itemText[..firstDot].Trim(),
                        itemText[(firstDot + 1)..].Trim()));
                }
            }
        }

        return results;
    }

    private static LegalSection? FindArticle(List<LegalSection> sections, Func<LegalSection, bool> predicate)
    {
        foreach (var s in sections)
        {
            if (s.Type == "article" && predicate(s)) return s;
            foreach (var child in s.Children)
            {
                if (child.Type == "article" && predicate(child)) return child;
                var found = child.Children.FirstOrDefault(c => c.Type == "article" && predicate(c));
                if (found is not null) return found;
            }
        }
        return null;
    }

    // ── Plain text extraction ──────────────────────────────────────────

    private static string ExtractPlainText(HtmlDocument doc)
    {
        // Extract all text from the main content div, cleaned up
        var body = doc.DocumentNode.SelectSingleNode("//div") ?? doc.DocumentNode;
        var text = NormalizeText(body.InnerText);
        // Collapse multiple blank lines
        text = Regex.Replace(text, @"\n{3,}", "\n\n");
        return text.Trim();
    }

    // ── Helpers ────────────────────────────────────────────────────────

    private static string NormalizeText(string html)
    {
        var decoded = WebUtility.HtmlDecode(html);
        // Normalize whitespace but preserve newlines
        decoded = Regex.Replace(decoded, @"[ \t]+", " ");
        decoded = Regex.Replace(decoded, @" *\n *", "\n");
        return decoded.Trim();
    }

    /// <summary>Collapse all whitespace (including newlines) to single spaces.</summary>
    private static string CollapseWhitespace(string text)
        => Regex.Replace(text, @"\s+", " ").Trim();

    private static bool IsAfterNode(HtmlNode a, HtmlNode b)
    {
        return a.StreamPosition > b.StreamPosition;
    }

    private static bool IsAfterOrEqual(HtmlNode a, HtmlNode b)
    {
        return a.StreamPosition >= b.StreamPosition;
    }

    private static bool Contains(this HtmlNode parent, HtmlNode target)
    {
        var current = target;
        while (current is not null)
        {
            if (current == parent) return true;
            current = current.ParentNode;
        }
        return false;
    }
}
