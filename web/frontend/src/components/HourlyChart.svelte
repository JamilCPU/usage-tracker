<script lang="ts">
  import type { HourlyRow } from "../lib/api";
  import { fmtDuration, hourLabel, palette } from "../lib/format";
  import { buildHourly, OTHER } from "../lib/series";

  let { rows, hourFrom, hourTo }: { rows: HourlyRow[]; hourFrom: number; hourTo: number } = $props();

  const W = 480;
  const H = 200;
  const PAD = { l: 38, r: 6, t: 8, b: 22 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const slot = plotW / 24;

  const series = $derived(buildHourly(rows));
  const colorOf = (name: string): string => (name === OTHER ? palette(-1) : palette(series.names.indexOf(name)));
  const scale = $derived(series.maxTotal > 0 ? plotH / series.maxTotal : 0);

  type Seg = { name: string; seconds: number; y: number; h: number };
  const bars = $derived(
    series.hours.map((b, hour) => {
      let y = PAD.t + plotH;
      const segs: Seg[] = [];
      for (const name of series.names) {
        const s = b[name] ?? 0;
        if (s <= 0) continue;
        const h = s * scale;
        y -= h;
        segs.push({ name, seconds: s, y, h });
      }
      return { hour, segs, total: segs.reduce((a, c) => a + c.seconds, 0) };
    }),
  );

  let tip = $state<{ hour: number; name: string; seconds: number; x: number; y: number } | null>(null);

  function show(hour: number, seg: Seg): void {
    tip = { hour, name: seg.name, seconds: seg.seconds, x: PAD.l + hour * slot + slot / 2, y: seg.y };
  }
</script>

<section class="card" aria-label="Usage by hour">
  <h2>By hour</h2>
  {#if series.maxTotal === 0}
    <p class="empty">No usage in this range.</p>
  {:else}
    <div class="wrap">
      <svg viewBox="0 0 {W} {H}" role="img" aria-label="Stacked bars of usage per hour of day">
        {#each [0, 0.5, 1] as t (t)}
          <line x1={PAD.l} x2={W - PAD.r} y1={PAD.t + plotH * (1 - t)} y2={PAD.t + plotH * (1 - t)} class="grid" />
          <text x={PAD.l - 4} y={PAD.t + plotH * (1 - t) + 3} text-anchor="end" class="axis">
            {fmtDuration(series.maxTotal * t)}
          </text>
        {/each}
        {#each bars as b (b.hour)}
          {@const dim = b.hour < hourFrom || b.hour > hourTo}
          <g opacity={dim ? 0.25 : 1}>
            {#each b.segs as s (s.name)}
              <rect
                x={PAD.l + b.hour * slot + 1}
                width={slot - 2}
                y={s.y}
                height={s.h}
                fill={colorOf(s.name)}
                role="presentation"
                onmouseenter={() => show(b.hour, s)}
                onmouseleave={() => (tip = null)}
                onclick={() => show(b.hour, s)}
              />
            {/each}
          </g>
          {#if b.hour % 3 === 0}
            <text x={PAD.l + b.hour * slot + slot / 2} y={H - 6} text-anchor="middle" class="axis">{hourLabel(b.hour)}</text>
          {/if}
        {/each}
      </svg>
      {#if tip}
        <div class="tip" style="left:{(tip.x / W) * 100}%; top:{(tip.y / H) * 100}%">
          <strong>{hourLabel(tip.hour)}</strong> {tip.name}: {fmtDuration(tip.seconds)}
        </div>
      {/if}
    </div>
    <ul class="legend">
      {#each series.names as n (n)}
        <li><span class="dot" style="background:{colorOf(n)}"></span>{n}</li>
      {/each}
    </ul>
  {/if}
</section>

<style>
  .wrap {
    position: relative;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .grid {
    stroke: var(--border);
    stroke-width: 1;
  }
  .axis {
    fill: var(--muted);
    font-size: 9px;
  }
  rect {
    cursor: pointer;
  }
  .tip {
    position: absolute;
    transform: translate(-50%, -110%);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 12px;
    white-space: nowrap;
    pointer-events: none;
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
