using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.EntityFrameworkCore;
using Microsoft.IdentityModel.Tokens;
using OpenRAG.Api.Data;
using OpenRAG.Api.Models.Entities;

namespace OpenRAG.Api.Services;

public class AuthService(AppDbContext db, IConfiguration config)
{
    public const string DefaultJwtSecret = "OpenRAG-Default-Secret-Key-Change-In-Production-2024!";

    private string JwtSecret => config["Jwt:Secret"] ?? DefaultJwtSecret;
    private int JwtExpiryDays => int.Parse(config["Jwt:ExpiryDays"] ?? "30");

    public async Task<User?> ValidateAsync(string username, string password, CancellationToken ct = default)
    {
        var user = await db.Users.FirstOrDefaultAsync(u => u.Username == username, ct);
        if (user is null) return null;
        if (!BCrypt.Net.BCrypt.Verify(password, user.PasswordHash)) return null;
        return user;
    }

    public string GenerateToken(User user)
    {
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(JwtSecret));
        var creds = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);

        var claims = new[]
        {
            new Claim(ClaimTypes.NameIdentifier, user.Id.ToString()),
            new Claim(ClaimTypes.Name, user.Username),
            new Claim(ClaimTypes.Role, user.Role),
            new Claim("displayName", user.DisplayName),
        };

        var token = new JwtSecurityToken(
            issuer: "OpenRAG",
            audience: "OpenRAG",
            claims: claims,
            expires: DateTime.UtcNow.AddDays(JwtExpiryDays),
            signingCredentials: creds
        );

        return new JwtSecurityTokenHandler().WriteToken(token);
    }

    public async Task<User> CreateUserAsync(string username, string password, string displayName, string role, CancellationToken ct = default)
    {
        var hash = BCrypt.Net.BCrypt.HashPassword(password);
        var user = new User
        {
            Username = username,
            PasswordHash = hash,
            DisplayName = displayName,
            Role = role,
            CreatedAt = DateTime.UtcNow,
        };
        db.Users.Add(user);
        await db.SaveChangesAsync(ct);
        return user;
    }

    public async Task EnsureDefaultUsersAsync(CancellationToken ct = default)
    {
        if (!await db.Users.AnyAsync(u => u.Role == Roles.Admin, ct))
            await CreateUserAsync("admin", "admin123", "Administrator", Roles.Admin, ct);

        if (!await db.Users.AnyAsync(u => u.Username == "user", ct))
            await CreateUserAsync("user", "user123", "Người dùng", Roles.User, ct);
    }

    public async Task<User?> GetByIdAsync(int id, CancellationToken ct = default)
    {
        return await db.Users.FindAsync([id], ct);
    }

    public async Task<List<User>> ListUsersAsync(CancellationToken ct = default)
    {
        return await db.Users.OrderBy(u => u.Username).ToListAsync(ct);
    }

    public async Task<bool> ChangePasswordAsync(int userId, string newPassword, CancellationToken ct = default)
    {
        var user = await db.Users.FindAsync([userId], ct);
        if (user is null) return false;
        user.PasswordHash = BCrypt.Net.BCrypt.HashPassword(newPassword);
        await db.SaveChangesAsync(ct);
        return true;
    }

    public async Task<bool> DeleteUserAsync(int userId, CancellationToken ct = default)
    {
        var user = await db.Users.FindAsync([userId], ct);
        if (user is null) return false;
        db.Users.Remove(user);
        await db.SaveChangesAsync(ct);
        return true;
    }
}
