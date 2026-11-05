<script lang="ts">
  import type { DailyRow } from "../lib/api";
  import { fmtDuration, palette } from "../lib/format";
  import { totalsByDevice } from "../lib/series";

  let { rows }: { rows: DailyRow[] } = $props();
  const totals = $derived(totalsByDevice(rows));
</script>

<section class="card" aria-label="Device comparison">
  <h2>Devices</h2>
  <p class="muted caption">all hours, all apps</p>
  {#if totals.length === 0}
    <p class="empty">No usage in this range.</p>
  {:else}
    <div class="cards">
      {#each totals as t, i (t.device)}
        <div class="dev" style="border-top-color:{palette(i)}">
          <div class="name">{t.device}</div>
          <div class="big">{fmtDuration(t.seconds)}</div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
  }
  .dev {
    background: var(--surface-2);
    border-top: 3px solid var(--accent);
    border-radius: 8px;
    padding: 10px 12px;
  }
  .name {
    font-size: 13px;
    color: var(--muted);
  }
  .big {
    font-size: 22px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
</style>
