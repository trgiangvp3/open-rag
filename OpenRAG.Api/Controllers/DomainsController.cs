using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/domains")]
public class DomainsController(AppDbContext db) : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> List(CancellationToken ct = default)
    {
        var domains = await db.Domains
            .Include(d => d.Children)
            .Where(d => d.ParentId == null)
            .OrderBy(d => d.Name)
            .Select(d => new
            {
                d.Id, d.Name, d.Slug,
                children = d.Children.OrderBy(c => c.Name).Select(c => new
                {
                    c.Id, c.Name, c.Slug,
                }).ToList(),
            })
            .ToListAsync(ct);

        return Ok(new { domains });
    }

    [HttpPost]
    public async Task<IActionResult> Create([FromBody] CreateDomainRequest req, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(req.Name))
            return BadRequest(new { detail = "Name is required" });

        var slug = req.Slug ?? GenerateSlug(req.Name);
        if (await db.Domains.AnyAsync(d => d.Slug == slug, ct))
            return Conflict(new { detail = $"Slug '{slug}' already exists" });

        if (req.ParentId.HasValue && !await db.Domains.AnyAsync(d => d.Id == req.ParentId, ct))
            return BadRequest(new { detail = "Parent domain not found" });

        var domain = new Domain { Name = req.Name, Slug = slug, ParentId = req.ParentId };
        db.Domains.Add(domain);
        await db.SaveChangesAsync(ct);
        return Ok(new { domain.Id, domain.Name, domain.Slug, domain.ParentId });
    }

    [HttpDelete("{id}")]
    public async Task<IActionResult> Delete(int id, CancellationToken ct = default)
    {
        var domain = await db.Domains.Include(d => d.Children).FirstOrDefaultAsync(d => d.Id == id, ct);
        if (domain is null) return NotFound();
        if (domain.Children.Count > 0)
            return BadRequest(new { detail = "Cannot delete domain with children. Delete children first." });

        // Unset DomainId on documents using this domain
        await db.Documents.Where(d => d.DomainId == id).ExecuteUpdateAsync(s => s.SetProperty(d => d.DomainId, (int?)null), ct);

        db.Domains.Remove(domain);
        await db.SaveChangesAsync(ct);
        return Ok(new { status = "ok" });
    }

    private static string GenerateSlug(string name)
    {
        return name.ToLowerInvariant()
            .Replace("đ", "d").Replace("Đ", "d")
            .Replace(" - ", "-").Replace(" ", "-")
            .Replace(".", "").Replace(",", "");
    }
}

public record CreateDomainRequest(string Name, string? Slug = null, int? ParentId = null);
