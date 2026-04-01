using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using OpenRAG.Api.Models.Entities;
using OpenRAG.Api.Services;

namespace OpenRAG.Api.Controllers;

[ApiController]
[Route("api/auth")]
public class AuthController(AuthService auth) : ControllerBase
{
    public record LoginRequest(string Username, string Password);
    public record CreateUserRequest(string Username, string Password, string DisplayName, string Role);
    public record ChangePasswordRequest(string NewPassword);

    [HttpPost("login")]
    public async Task<IActionResult> Login([FromBody] LoginRequest req, CancellationToken ct)
    {
        var user = await auth.ValidateAsync(req.Username, req.Password, ct);
        if (user is null)
            return Unauthorized(new { message = "Sai tên đăng nhập hoặc mật khẩu" });

        var token = auth.GenerateToken(user);
        return Ok(new
        {
            token,
            user = new { user.Id, user.Username, user.DisplayName, user.Role }
        });
    }

    [Authorize]
    [HttpGet("me")]
    public async Task<IActionResult> Me(CancellationToken ct)
    {
        var idClaim = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (idClaim is null || !int.TryParse(idClaim, out var userId))
            return Unauthorized();

        var user = await auth.GetByIdAsync(userId, ct);
        if (user is null) return Unauthorized();

        return Ok(new { user.Id, user.Username, user.DisplayName, user.Role });
    }

    // ── Admin-only: User management ──────────────────────────────────────

    [Authorize(Roles = Roles.Admin)]
    [HttpGet("users")]
    public async Task<IActionResult> ListUsers(CancellationToken ct)
    {
        var users = await auth.ListUsersAsync(ct);
        return Ok(users.Select(u => new { u.Id, u.Username, u.DisplayName, u.Role, u.CreatedAt }));
    }

    [Authorize(Roles = Roles.Admin)]
    [HttpPost("users")]
    public async Task<IActionResult> CreateUser([FromBody] CreateUserRequest req, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(req.Username) || req.Username.Trim().Length > 50)
            return BadRequest(new { message = "Username không hợp lệ (1-50 ký tự)" });

        if (string.IsNullOrWhiteSpace(req.Password) || req.Password.Length < 6 || req.Password.Length > 72)
            return BadRequest(new { message = "Password phải có 6-72 ký tự" });

        if (string.IsNullOrWhiteSpace(req.DisplayName) || req.DisplayName.Trim().Length > 100)
            return BadRequest(new { message = "Tên hiển thị không hợp lệ (1-100 ký tự)" });

        if (req.Role is not (Roles.Admin or Roles.User))
            return BadRequest(new { message = $"Role phải là '{Roles.Admin}' hoặc '{Roles.User}'" });

        try
        {
            var user = await auth.CreateUserAsync(req.Username.Trim(), req.Password, req.DisplayName.Trim(), req.Role, ct);
            return Ok(new { user.Id, user.Username, user.DisplayName, user.Role });
        }
        catch (Exception)
        {
            return Conflict(new { message = "Tên đăng nhập đã tồn tại" });
        }
    }

    [Authorize(Roles = Roles.Admin)]
    [HttpPut("users/{id}/password")]
    public async Task<IActionResult> ChangePassword(int id, [FromBody] ChangePasswordRequest req, CancellationToken ct)
    {
        if (string.IsNullOrWhiteSpace(req.NewPassword) || req.NewPassword.Length < 6 || req.NewPassword.Length > 72)
            return BadRequest(new { message = "Mật khẩu mới phải có 6-72 ký tự" });

        var ok = await auth.ChangePasswordAsync(id, req.NewPassword, ct);
        return ok ? Ok(new { ok = true }) : NotFound();
    }

    [Authorize(Roles = Roles.Admin)]
    [HttpDelete("users/{id}")]
    public async Task<IActionResult> DeleteUser(int id, CancellationToken ct)
    {
        var myId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (myId == id.ToString())
            return BadRequest(new { message = "Không thể xoá chính mình" });

        var ok = await auth.DeleteUserAsync(id, ct);
        return ok ? Ok(new { ok = true }) : NotFound();
    }
}
