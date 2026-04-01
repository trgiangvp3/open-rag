import { onUnmounted } from 'vue'
import * as signalR from '@microsoft/signalr'

let sharedConnection: signalR.HubConnection | null = null
let refCount = 0
const listeners = new Map<string, Set<(event: any) => void>>()

function dispatch(eventName: string, event: any) {
  for (const handler of listeners.get(eventName) ?? []) handler(event)
}

async function ensureConnection() {
  if (sharedConnection) {
    const state = sharedConnection.state
    if (state === signalR.HubConnectionState.Connected || state === signalR.HubConnectionState.Connecting || state === signalR.HubConnectionState.Reconnecting) {
      return
    }
    // Disconnected — stop and recreate
    try { await sharedConnection.stop() } catch {}
    sharedConnection = null
  }

  sharedConnection = new signalR.HubConnectionBuilder()
    .withUrl('/ws/progress')
    .withAutomaticReconnect([0, 2000, 5000, 10000])
    .build()

  sharedConnection.onclose(() => { sharedConnection = null })

  // Register all known event names on the new connection
  for (const eventName of listeners.keys()) {
    sharedConnection.on(eventName, (event: any) => dispatch(eventName, event))
  }

  try {
    await sharedConnection.start()
  } catch (e) {
    console.warn('SignalR connection failed:', e)
    sharedConnection = null
  }
}

/**
 * Composable that subscribes to a SignalR progress hub event.
 * Shares a single connection across all components.
 * Automatically cleans up on unmount.
 */
export function useProgressHub(eventName: string, handler: (event: any) => void) {
  if (!listeners.has(eventName)) {
    listeners.set(eventName, new Set())
    // If already connected, register the new event on existing connection
    if (sharedConnection?.state === signalR.HubConnectionState.Connected) {
      sharedConnection.on(eventName, (event: any) => dispatch(eventName, event))
    }
  }
  listeners.get(eventName)!.add(handler)
  refCount++

  async function connect() {
    await ensureConnection()
  }

  onUnmounted(() => {
    listeners.get(eventName)?.delete(handler)
    if (listeners.get(eventName)?.size === 0) {
      listeners.delete(eventName)
      sharedConnection?.off(eventName)
    }
    refCount--
    if (refCount <= 0 && sharedConnection) {
      sharedConnection.stop().catch(() => {})
      sharedConnection = null
      refCount = 0
    }
  })

  return { connect }
}
