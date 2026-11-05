<script lang="ts">
  import type { DailyRow } from "../lib/api";
  import { fmtDay, fmtDuration, palette } from "../lib/format";

  let { rows }: { rows: DailyRow[] } = $props();

  const W = 480;
  const H = 180;
  const PAD = { l: 38, r: 6, t: 8, b: 22 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;

  const days = $derived([...new Set(rows.map((r) => r.day))].sort());
  const devices = $derived([...new Set(rows.map((r) => r.device))].sort());
  const max = $derived(Math.max(1, ...rows.map((r) => r.seconds)));
  const groupW = $derived(plotW / Math.max(1, days.length));
  const barW = $derived(Math.max(2, (groupW - 4) / Math.max(1, devices.length)));
  const labelEvery = $derived(Math.ceil(days.length / 7));

  function seconds(day: string, device: string): number {
    return rows.find((r) => r.day === day && r.device === device)?.seconds ?? 0;
  }
</script>

<section class="card" aria-label="Daily totals">
  <h2>Daily totals</h2>
  <p class="muted caption">all hours, all apps</p>
  {#if rows.length === 0}
    <p class="empty">No usage in this range.</p>
  {:else}
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Bars of total usage per day, grouped by device">
      {#each [0, 0.5, 1] as t (t)}
        <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t + plotH * (1 - t)} y2={PAD.t + plotH * (1 - t)} class="grid" />
        <text x={PAD.l - 4} y={PAD.t + plotH * (1 - t) + 3} text-anchor="end" class="axis">{fmtDuration(max * t)}</text>
      {/each}
      {#each days as day, di (day)}
        {#each devices as dev, vi (dev)}
          {@const s = seconds(day, dev)}
          {#if s > 0}
            <rect
              x={PAD.l + di * groupW + 2 + vi * barW}
              width={barW - 1}
              y={PAD.t + plotH - (s / max) * plotH}
              height={(s / max) * plotH}
              fill={palette(vi)}
            ><title>{dev} {fmtDay(day)}: {fmtDuration(s)}</title></rect>
          {/if}
        {/each}
        {#if di % labelEvery === 0}
          <text x={PAD.l + di * groupW + groupW / 2} y={H - 6} text-anchor="middle" class="axis">{fmtDay(day)}</text>
        {/if}
      {/each}
    </svg>
    <ul class="legend">
      {#each devices as dev, vi (dev)}
        <li><span class="dot" style="background:{palette(vi)}"></span>{dev}</li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .grid {
    stroke: var(--border);
  }
  .axis {
    fill: var(--muted);
    font-size: 9px;
  }
  .legend {
    list-style: none;
    margin: 8px 0 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    font-size: 12px;
  }
  .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 2px;
    margin-right: 5px;
  }
</style>
