using Microsoft.AspNetCore.SignalR;

namespace OpenRAG.Api.Hubs;

/// <summary>SignalR hub for real-time indexing progress events.</summary>
/// <remarks>Server-push only — clients listen for "progress" events.</remarks>
public class ProgressHub : Hub { }
