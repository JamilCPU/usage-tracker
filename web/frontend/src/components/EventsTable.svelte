<script lang="ts">
  import { getEvents, type UsageEvent } from "../lib/api";
  import type { Filters } from "../lib/filters";

  let { filters }: { filters: Filters } = $props();

  let rows = $state<UsageEvent[]>([]);
  let cursor = $state<string | null>(null);
  let loading = $state(false);
  let error = $state("");
  let token = 0;

  async function load(reset: boolean): Promise<void> {
    const mine = ++token;
    loading = true;
    error = "";
    try {
      const page = await getEvents(filters, reset ? null : cursor);
      if (mine !== token) return;
      rows = reset ? page.rows : [...rows, ...page.rows];
      cursor = page.next_cursor;
    } catch (e: unknown) {
      if (mine === token) error = e instanceof Error ? e.message : "Failed to load events";
    } finally {
      if (mine === token) loading = false;
    }
  }

  $effect(() => {
    void [filters.devices.join(), filters.from, filters.to, filters.hourFrom, filters.hourTo, filters.app, filters.domain];
    void load(true);
  });

  function time(iso: string): string {
    return iso.slice(5, 16).replace("T", " ");
  }
</script>

<section class="card" aria-label="Events">
  <h2>Events</h2>
  {#if error}<p class="err">{error}</p>{/if}
  {#if rows.length === 0 && !loading && !error}
    <p class="empty">No events.</p>
  {:else}
    <div class="scroll">
      <table>
        <thead><tr><th>Time</th><th>Device</th><th>App</th><th>Title</th></tr></thead>
        <tbody>
          {#each rows as e (e.id)}
            <tr>
              <td class="nowrap">{time(e.ts_start)}</td>
              <td>{e.device}</td>
              <td>{e.app ?? ""}</td>
              <td class="title" title={e.url ?? ""}>{e.title ?? e.domain ?? ""}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  {#if cursor}
    <button type="button" class="more" disabled={loading} onclick={() => load(false)}>
      {loading ? "Loading..." : "Load more"}
    </button>
  {/if}
</section>

<style>
  .scroll {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th,
  td {
    text-align: left;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border);
  }
  th {
    color: var(--muted);
    font-weight: 500;
  }
  .nowrap {
    white-space: nowrap;
  }
  .title {
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .more {
    margin-top: 10px;
    width: 100%;
  }
  .err {
    color: var(--danger);
  }
</style>
