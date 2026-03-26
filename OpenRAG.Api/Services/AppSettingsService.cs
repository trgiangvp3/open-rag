using Microsoft.EntityFrameworkCore;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class AppSettingsService(AppDbContext db)
{
    public async Task<Dictionary<string, string>> GetAllAsync(CancellationToken ct = default)
    {
        return await db.AppSettings.ToDictionaryAsync(s => s.Key, s => s.Value, ct);
    }

    public async Task<string?> GetAsync(string key, CancellationToken ct = default)
    {
        var setting = await db.AppSettings.FindAsync([key], ct);
        return setting?.Value;
    }

    public async Task SetAsync(string key, string value, CancellationToken ct = default)
    {
        var setting = await db.AppSettings.FindAsync([key], ct);
        if (setting is null)
        {
            db.AppSettings.Add(new AppSetting { Key = key, Value = value });
        }
        else
        {
            setting.Value = value;
        }
        await db.SaveChangesAsync(ct);
    }

    public async Task SetManyAsync(Dictionary<string, string> settings, CancellationToken ct = default)
    {
        foreach (var (key, value) in settings)
        {
            var setting = await db.AppSettings.FindAsync([key], ct);
            if (setting is null)
                db.AppSettings.Add(new AppSetting { Key = key, Value = value });
            else
                setting.Value = value;
        }
        await db.SaveChangesAsync(ct);
    }
}
