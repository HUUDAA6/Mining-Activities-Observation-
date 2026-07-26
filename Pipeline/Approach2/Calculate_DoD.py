import os
import sys
import json
import numpy as np
import rasterio
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LightSource, TwoSlopeNorm
import matplotlib.patches as mpatches
import contextily as ctx
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer

from report_theme import (
    HEX, ST_VAL_POS, ST_VAL_NEG,
    header_strip, section_major, section_minor, info_table, img_block, footer,
    val_style_auto, fmt_vol_clean as _fmt_vol_theme,
    apply_matplotlib_defaults,
)

apply_matplotlib_defaults()


STEP = "[Calculate_DoD]"


def log(msg, level="INFO"):
    print(f"{STEP} {level}: {msg}", flush=True)


def fail(reason, hint=None, code=1):
    log(reason, level="ERROR")
    if hint:
        log(f"Suggested fix: {hint}", level="HINT")
    sys.exit(code)


def _dod_norm(dod_masked):
    """
    Diverging norm using the real data min and max, centered at 0.
    If data spans both signs : TwoSlopeNorm (unequal arms).
    If all one sign : linear Normalize over the actual range.
    """
    from matplotlib.colors import Normalize
    real_min = float(np.nanmin(dod_masked))
    real_max = float(np.nanmax(dod_masked))
    if real_min < 0 < real_max:
        return TwoSlopeNorm(vmin=real_min, vcenter=0, vmax=real_max)
    # All same sign: fallback to linear scale over the true range
    return Normalize(vmin=real_min, vmax=real_max)


def _dod_rgba(dod_data, norm, alpha=0.65, lod=0.0):
    """Convert DoD array to RGBA: NaN : fully transparent, data : semi-transparent.
    Pixels below the Limit of Detection (Δz < lod) are also rendered transparent
    so noise does not visually fill the whole AOI.
    """
    valid = np.isfinite(dod_data) & (dod_data > -9000)
    significant = valid & (np.abs(dod_data) >= lod)
    cmap  = plt.get_cmap('RdBu')
    rgba  = cmap(norm(np.where(significant, dod_data, 0)))
    rgba[~significant, 3] = 0.0
    rgba[significant,  3] = alpha
    return rgba, significant


def create_dod_map(dod_path, output_png_path, stats):
    """
    Plan-view DoD map with symmetric diverging colormap.
    Blue = accretion (elevation gain), Red = erosion (elevation loss).
    """
    with rasterio.open(dod_path) as src:
        dod    = src.read(1).astype(np.float32)
        bounds = src.bounds

    lod    = float(stats.get('lod_95_m', 0.0) or 0.0)
    valid  = np.isfinite(dod) & (dod > -9000)
    # Mask sub-LoD noise so only statistically significant change is drawn.
    significant = valid & (np.abs(dod) >= lod)
    masked = np.where(significant, dod, np.nan)
    norm   = _dod_norm(np.ma.masked_invalid(masked))
    cmap   = plt.get_cmap('RdBu')

    aspect = dod.shape[0] / dod.shape[1]
    fig, ax = plt.subplots(figsize=(10, 10 * aspect))

    im = ax.imshow(
        masked, cmap=cmap, norm=norm,
        extent=[bounds.left, bounds.right, bounds.bottom, bounds.top],
        origin='upper', interpolation='bilinear'
    )

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('Elevation Change (m)', rotation=270, labelpad=18, fontweight='bold')

    # Key numbers on the map
    stats_txt = (
        f"Mean change : {stats['mean_change_m']:+.2f} m\n"
        f"LoD (95%)   : ±{lod:.2f} m\n"
        f"Net volume  : {stats['net_vol_m3']:+,.0f} m³\n"
        f"Gain area   : {stats['gain_area_ha']:.2f} ha\n"
        f"Loss area   : {stats['loss_area_ha']:.2f} ha"
    )
    ax.text(0.02, 0.98, stats_txt, transform=ax.transAxes,
            fontsize=8, va='top', family='monospace',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='white', alpha=0.88))

    legend = [
        mpatches.Patch(color='#2166AC', label='Accretion / Gain (+)'),
        mpatches.Patch(color='#B2182B', label='Erosion / Loss (−)'),
    ]
    ax.legend(handles=legend, loc='lower right', fontsize=8, framealpha=0.88)

    ax.set_xlabel('Longitude (°)', fontsize=9)
    ax.set_ylabel('Latitude (°)', fontsize=9)
    ax.set_title('DEM of Difference — Plan View', fontweight='bold', fontsize=13, pad=10)

    fig.tight_layout()
    fig.savefig(output_png_path, dpi=250, bbox_inches='tight')
    plt.close(fig)


def create_dod_3d_overlay(dod_path, copernicus_path, output_png_path, lod=0.0):
    """
    Terrain hillshade from Copernicus with DoD colours overlaid at 65% opacity.
    Gives the 3D relief + change-location view shown in the reference image.
    """
    with rasterio.open(copernicus_path) as src:
        terrain    = src.read(1).astype(np.float32)
        cop_nodata = src.nodata
        res_x      = abs(src.transform.a)
        res_y      = abs(src.transform.e)
        bounds     = src.bounds

    with rasterio.open(dod_path) as src:
        dod = src.read(1).astype(np.float32)

    # Clean terrain for hillshade
    t_valid = np.isfinite(terrain) & (terrain > -9000)
    if cop_nodata is not None:
        t_valid &= (terrain != cop_nodata)
    terrain = np.where(t_valid, terrain, float(np.nanmean(terrain[t_valid])))

    dx_m = res_x * 111320
    dy_m = res_y * 111320
    ls   = LightSource(azdeg=315, altdeg=45)
    hillshade = ls.hillshade(terrain, vert_exag=2, dx=dx_m, dy=dy_m)

    dod_masked = np.ma.masked_where(
        ~(np.isfinite(dod) & (dod > -9000) & (np.abs(dod) >= lod)), dod
    )
    norm       = _dod_norm(dod_masked)
    rgba, _    = _dod_rgba(dod, norm, alpha=0.65, lod=lod)

    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    aspect = terrain.shape[0] / terrain.shape[1]
    fig, ax = plt.subplots(figsize=(10, 10 * aspect))

    ax.imshow(hillshade, cmap='gray', vmin=0, vmax=1,
              extent=extent, origin='upper', interpolation='bilinear')
    ax.imshow(rgba, extent=extent, origin='upper', interpolation='bilinear')

    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap('RdBu'), norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('Elevation Change (m)', rotation=270, labelpad=18, fontweight='bold')

    legend = [
        mpatches.Patch(color='#2166AC', label='Accretion / Gain (+)'),
        mpatches.Patch(color='#B2182B', label='Erosion / Loss (−)'),
    ]
    ax.legend(handles=legend, loc='lower right', fontsize=8, framealpha=0.88)

    ax.set_xlabel('Longitude (°)', fontsize=9)
    ax.set_ylabel('Latitude (°)', fontsize=9)
    ax.set_title('DoD on Terrain Hillshade — 3D Relief View',
                 fontweight='bold', fontsize=13, pad=10)

    fig.tight_layout()
    fig.savefig(output_png_path, dpi=250, bbox_inches='tight')
    plt.close(fig)


def create_dod_context_overlay(dod_path, output_png_path, lod=0.0):
    """
    DoD semi-transparent overlay on Esri WorldImagery satellite tiles.
    Falls back to a plain grey background if satellite tiles are unavailable
    (no internet in the Airflow worker environment).
    """
    with rasterio.open(dod_path) as src:
        dod    = src.read(1).astype(np.float32)
        bounds = src.bounds

    dod_masked = np.ma.masked_where(
        ~(np.isfinite(dod) & (dod > -9000) & (np.abs(dod) >= lod)), dod
    )
    norm       = _dod_norm(dod_masked)
    rgba, _    = _dod_rgba(dod, norm, alpha=0.60, lod=lod)

    extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
    aspect = dod.shape[0] / dod.shape[1]
    fig, ax = plt.subplots(figsize=(10, 10 * aspect))

    ax.set_xlim(bounds.left,  bounds.right)
    ax.set_ylim(bounds.bottom, bounds.top)
    ax.set_facecolor('#D0D8E0')

    basemap_ok = True
    try:
        ctx.add_basemap(ax, crs='EPSG:4326',
                        source=ctx.providers.Esri.WorldImagery, zoom='auto')
    except Exception as e:
        basemap_ok = False
        print(
            f"{STEP} WARN: satellite tiles unavailable ({e.__class__.__name__}: {e}); "
            "rendering DoD on plain background.",
            flush=True,
        )
        ax.text(0.5, 0.01,
                "Satellite tiles unavailable : no internet access in worker",
                transform=ax.transAxes, fontsize=7, color='#555555',
                ha='center', va='bottom',
                bbox=dict(facecolor='white', alpha=0.7, boxstyle='round,pad=0.3'))

    ax.imshow(rgba, extent=extent, origin='upper',
              interpolation='bilinear', zorder=2)

    sm = plt.cm.ScalarMappable(cmap=plt.get_cmap('RdBu'), norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label('Elevation Change (m)', rotation=270, labelpad=18, fontweight='bold')

    legend = [
        mpatches.Patch(color='#2166AC', label='Accretion / Gain (+)'),
        mpatches.Patch(color='#B2182B', label='Erosion / Loss (−)'),
    ]
    ax.legend(handles=legend, loc='lower right', fontsize=8, framealpha=0.88)

    title = 'DoD on Satellite Imagery' if basemap_ok else 'DoD : Location Map (no satellite tiles)'
    ax.set_title(title, fontweight='bold', fontsize=13, pad=10)
    ax.axis('off')

    fig.tight_layout()
    fig.savefig(output_png_path, dpi=250, bbox_inches='tight')
    plt.close(fig)


def create_dod_histogram(valid_dod, output_png_path, lod=0.0):
    """Renders a histogram of elevation change values with gain/loss colouring.
    The LoD band is shaded grey to show the range treated as statistical noise.
    """
    fig, ax = plt.subplots(figsize=(9, 4))

    bins = min(120, max(40, len(valid_dod) // 500))
    counts, edges, patches = ax.hist(valid_dod, bins=bins, edgecolor='none')

    for patch, left, right in zip(patches, edges[:-1], edges[1:]):
        mid = 0.5 * (left + right)
        if abs(mid) < lod:
            patch.set_facecolor('#B0B0B0')  # noise band
        else:
            patch.set_facecolor(HEX["success"] if left >= 0 else HEX["danger"])

    if lod > 0:
        ax.axvspan(-lod, lod, color='#B0B0B0', alpha=0.25,
                   label=f'±LoD (±{lod:.2f} m)')
    ax.axvline(0, color=HEX["primary"], linewidth=1.5, linestyle='--', label='No change')
    ax.axvline(float(np.mean(valid_dod)), color=HEX["warning"], linewidth=1.5,
               linestyle='-', label=f'Mean: {float(np.mean(valid_dod)):+.2f} m')

    ax.set_xlabel('Elevation Change (m)', fontweight='bold')
    ax.set_ylabel('Pixel Count', fontweight='bold')
    ax.set_title('Distribution of Elevation Change (DoD)', fontweight='bold', pad=10)
    ax.legend(framealpha=0.9)
    ax.spines[['top', 'right']].set_visible(False)

    fig.tight_layout()
    fig.savefig(output_png_path, dpi=200, transparent=False)
    plt.close(fig)


def _create_multiperiod_map(dod_paths, labels, output_png, site_name):
    """Three-panel side-by-side DoD comparison map."""
    from matplotlib.colors import TwoSlopeNorm as TSN, Normalize as Nrm
    fig, axes = plt.subplots(1, 3, figsize=(20, 7))
    cmap = plt.get_cmap('RdBu')

    legend = [
        mpatches.Patch(color=HEX["success"], label='Gain (+)'),
        mpatches.Patch(color=HEX["danger"],  label='Loss (−)'),
    ]

    for ax, path, label in zip(axes, dod_paths, labels):
        with rasterio.open(path) as src:
            dod = src.read(1).astype(np.float32)
            bounds = src.bounds

        valid = np.isfinite(dod) & (dod > -9000)
        masked = np.where(valid, dod, np.nan)
        rmin = float(np.nanmin(masked)); rmax = float(np.nanmax(masked))
        norm = TSN(vmin=rmin, vcenter=0, vmax=rmax) if rmin < 0 < rmax else Nrm(vmin=rmin, vmax=rmax)

        extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
        im = ax.imshow(masked, cmap=cmap, norm=norm, extent=extent,
                       origin='upper', interpolation='bilinear')
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, shrink=0.8)
        cbar.set_label('Elev. Change (m)', fontsize=8)

        vvals = dod[valid]
        mean_c = float(np.mean(vvals))
        ax.text(0.03, 0.97, f"Mean: {mean_c:+.2f} m",
                transform=ax.transAxes, fontsize=7, va='top',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.85))
        ax.set_title(label, fontweight='bold', fontsize=11)
        ax.axis('off')

    axes[0].legend(handles=legend, loc='lower left', fontsize=7, framealpha=0.88)
    fig.suptitle(f'{site_name} : Multi-Period Elevation Change',
                 fontweight='bold', fontsize=14, y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_png, dpi=250, bbox_inches='tight')
    plt.close(fig)
    print(f"  Multi-period map saved: {output_png}")


_fmt_vol_clean = _fmt_vol_theme


def generate_dod_report(site_name, output_dir, dod_path, stats):
    """Professional DoD report : styled via report_theme / report_style.json."""
    gen_date  = datetime.now().strftime("%B %d, %Y  -  %H:%M")
    safe_name = site_name.replace(" ", "_")
    report_path = os.path.join(output_dir, f"{safe_name}_DoD_Report.pdf")

    doc = SimpleDocTemplate(
        report_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )

    story = []
    story.extend(header_strip(
        f"{site_name.upper()} - DEM of Difference Report",
        f"Generated {gen_date}",
    ))

    story.append(section_major("Datasets Used"))
    story.append(Spacer(1, 4 * mm))
    story.append(info_table([
        ("Modern DEM",   "Copernicus GLO-30  (2011-2015)  |  ESA TanDEM-X"),
        ("Baseline DEM", "NASADEM  (February 2000)  |  NASA SRTM"),
        ("DoD Formula",  "Copernicus - NASADEM  (bias-corrected)"),
        ("Output file",  os.path.basename(dod_path)),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Coverage & Resolution"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Total valid pixels", f"{stats['n_valid_pixels']:,}"),
        ("Pixel area",         f"{stats['pixel_area_m2']:.1f} m^2"),
        ("Total valid area",   f"{stats['total_area_ha']:,.2f} ha  "
                               f"({stats['total_area_ha'] * 10000:,.0f} m^2)"),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Elevation Change Statistics"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Mean change",   f"{stats['mean_change_m']:+.4f} m",
         val_style_auto(stats['mean_change_m'])),
        ("Median change", f"{stats['median_change_m']:+.4f} m",
         val_style_auto(stats['median_change_m'])),
        ("Std deviation", f"{stats['std_change_m']:.4f} m"),
        ("Min change",    f"{stats['min_change_m']:+.4f} m", ST_VAL_NEG),
        ("Max change",    f"{stats['max_change_m']:+.4f} m", ST_VAL_POS),
    ]))
    story.append(Spacer(1, 7 * mm))

    story.append(section_minor("Gain / Loss Breakdown"))
    story.append(Spacer(1, 3 * mm))
    story.append(info_table([
        ("Area of elevation gain", f"{stats['gain_area_ha']:,.4f} ha", ST_VAL_POS),
        ("Area of elevation loss", f"{stats['loss_area_ha']:,.4f} ha", ST_VAL_NEG),
        ("Volume gained",          _fmt_vol_clean(stats['vol_gain_m3']), ST_VAL_POS),
        ("Volume lost",            _fmt_vol_clean(stats['vol_loss_m3']), ST_VAL_NEG),
        ("Net volumetric change",  _fmt_vol_clean(stats['net_vol_m3']),
         val_style_auto(stats['net_vol_m3'])),
    ]))
    story.append(Spacer(1, 7 * mm))

    for fname, title, ratio in [
        (f"{safe_name}_DoD_Map.png",       "DoD Map - Plan View",              1.0),
        (f"{safe_name}_DoD_3D.png",        "DoD on Terrain - 3D Relief View",  1.0),
        (f"{safe_name}_DoD_Satellite.png", "DoD on Satellite Imagery",         1.0),
        (f"{safe_name}_DoD_Histogram.png", "Elevation Change Distribution",    4 / 9),
    ]:
        p = os.path.join(output_dir, fname)
        if os.path.exists(p):
            story.append(section_minor(title))
            story.append(Spacer(1, 3 * mm))
            story.append(img_block(p, title, ratio=ratio))
            story.append(Spacer(1, 6 * mm))

    story.extend(footer(
        "Sources: ESA Copernicus GLO-30, NASA SRTM / NASADEM  |  "
        "DoD = Modern DEM - Baseline DEM (bias-corrected)  |  "
        "Positive = accretion, Negative = erosion  |  "
        f"Generated: {gen_date}"
    ))

    doc.build(story)
    return report_path



def main():
    log("Computing DEM of Difference (Copernicus − NASADEM)")

    if len(sys.argv) < 3:
        fail(
            "Missing arguments. Expected: python Calculate_DoD.py <site_name> <config_path>",
            hint="The DAG should pass these automatically; check resolve_site output.",
        )

    target_site_name = sys.argv[1].strip()
    config_file_path = sys.argv[2].strip()

    safe_folder_name = target_site_name.replace(" ", "_")
    output_dir       = os.path.join("DEM_Downloads", safe_folder_name)
    metadata_path    = os.path.join(output_dir, "fetch_metadata.json")

    if not os.path.exists(metadata_path):
        fail(
            f"fetch_metadata.json missing at {metadata_path}",
            hint="The align_dems task must have failed or been skipped. Re-run it first.",
        )

    with open(metadata_path) as f:
        meta = json.load(f)

    copernicus_path      = meta.get("copernicus")
    nasadem_aligned_path = meta.get("aligned_nasadem")

    if not copernicus_path or not nasadem_aligned_path:
        fail(
            "Metadata is missing 'copernicus' or 'aligned_nasadem' file path.",
            hint="The aligner did not finish — open its logs to see why.",
            code=0,
        )

    log(f"Reference: {copernicus_path}")
    log(f"Baseline:  {nasadem_aligned_path}")
    dod_path = os.path.join(output_dir, "DEM_of_Difference.tif")

    try:
        with rasterio.open(copernicus_path) as cop_src, \
             rasterio.open(nasadem_aligned_path) as nasa_src:

            if cop_src.shape != nasa_src.shape:
                fail(
                    f"Raster shapes do not match — Copernicus {cop_src.shape} vs NASADEM {nasa_src.shape}",
                    hint="The aligner produced a NASADEM whose grid does not match Copernicus. Re-run align_dems.",
                )
            if cop_src.crs != nasa_src.crs:
                fail(
                    f"CRS mismatch — Copernicus {cop_src.crs} vs NASADEM {nasa_src.crs}",
                    hint="Both DEMs must be EPSG:4326 after alignment. Re-run align_dems.",
                )

            # Cast to float32 BEFORE arithmetic to avoid int16 overflow and to
            # preserve sub-metre precision in the difference raster.
            cop_data  = cop_src.read(1).astype(np.float32)
            nasa_data = nasa_src.read(1).astype(np.float32)

            cop_nodata  = cop_src.nodata
            nasa_nodata = nasa_src.nodata

            # A pixel is only valid if BOTH inputs have real elevation data.
            # The -9000 floor catches datasets that use -9999 / -32768 sentinel
            # values without declaring a nodata tag.
            valid = np.ones(cop_data.shape, dtype=bool)

            if cop_nodata is not None:
                valid &= (cop_data != cop_nodata)
            valid &= (cop_data > -9000)

            if nasa_nodata is not None:
                valid &= (nasa_data != nasa_nodata)
            valid &= (nasa_data > -9000)

            dod_data = np.full(cop_data.shape, np.nan, dtype=np.float32)
            dod_data[valid] = cop_data[valid] - nasa_data[valid]

            # Resolution is in degrees; convert to metres at scene centre for
            # the volumetric estimates below.
            res_x_deg = abs(cop_src.transform.a)
            res_y_deg = abs(cop_src.transform.e)
            centre_lat = (cop_src.bounds.top + cop_src.bounds.bottom) / 2.0

            m_per_deg_lon = 111320.0 * np.cos(np.radians(centre_lat))
            m_per_deg_lat = 111320.0

            pixel_area_m2 = (res_x_deg * m_per_deg_lon) * (res_y_deg * m_per_deg_lat)

            # Stable Terrain Analysis & Statistical LoD:
            # Pick low-slope pixels where real change is unlikely. The std-dev
            # of the DoD on those pixels is the empirical combined error
            # σ_combined ≈ sqrt(σ₁² + σ₂²); LoD = t × σ_combined (t=1.96 for 95%).
            log("Running Stable Terrain Analysis…")
            try:
                # Slope from the reference DEM (Copernicus) in degrees
                dy = np.gradient(cop_data, res_y_deg * m_per_deg_lat, axis=0)
                dx = np.gradient(cop_data, res_x_deg * m_per_deg_lon, axis=1)
                slope_deg = np.degrees(np.arctan(np.sqrt(dx**2 + dy**2)))

                # Stable terrain: slope < 10° AND valid DoD pixel
                stable_mask = valid & (slope_deg < 10.0)
                n_stable_terrain = int(stable_mask.sum())
                stable_pct = n_stable_terrain / max(int(valid.sum()), 1) * 100

                if n_stable_terrain > 100:
                    stable_dod = dod_data[stable_mask]
                    stable_mean = float(np.mean(stable_dod))
                    stable_std  = float(np.std(stable_dod))
                    # NMAD = normalised median absolute deviation — robust to outliers
                    stable_nmad = float(1.4826 * np.median(
                        np.abs(stable_dod - np.median(stable_dod))))

                    sigma_combined = stable_nmad
                    t_value = 1.96
                    lod_95 = t_value * sigma_combined

                    log(f"  Stable terrain pixels: {n_stable_terrain:,} ({stable_pct:.1f}% of valid)")
                    log(f"  Stable mean:           {stable_mean:+.4f} m")
                    log(f"  Stable NMAD:           {stable_nmad:.4f} m")
                    log(f"  LoD (95% confidence):  ±{lod_95:.2f} m")
                else:
                    log(
                        f"Too few stable pixels ({n_stable_terrain}). "
                        "Falling back to published global accuracies.",
                        level="WARN",
                    )
                    sigma_nasadem    = 3.6   # NASADEM global RMSE (m)
                    sigma_copernicus = 4.0   # Copernicus GLO-30 global RMSE (m)
                    sigma_combined   = np.sqrt(sigma_nasadem**2 + sigma_copernicus**2)
                    stable_nmad      = float(sigma_combined)
                    stable_mean      = 0.0
                    stable_std       = float(sigma_combined)
                    t_value          = 1.96
                    lod_95           = t_value * sigma_combined
                    stable_pct       = 0.0
                    log(f"  Fallback LoD (95%): ±{lod_95:.2f} m")

            except Exception as e:
                log(
                    f"Stable terrain analysis failed ({e.__class__.__name__}: {e}). "
                    "Using published accuracy values instead.",
                    level="WARN",
                )
                sigma_nasadem    = 3.6
                sigma_copernicus = 4.0
                sigma_combined   = float(np.sqrt(sigma_nasadem**2 + sigma_copernicus**2))
                stable_nmad      = sigma_combined
                stable_mean      = 0.0
                stable_std       = sigma_combined
                stable_pct       = 0.0
                n_stable_terrain = 0
                t_value          = 1.96
                lod_95           = t_value * sigma_combined

            # Nuth & Kääb fixes horizontal + coarse vertical shifts, but a
            # sub-metre vertical residual typically remains. Subtracting the
            # stable-terrain mean removes that residual so quiet (non-mining)
            # sites do not read as "heavy loss/gain" from a flat global offset.
            if abs(stable_mean) > 1e-6:
                log(f"  Bias correction: subtracting {stable_mean:+.4f} m from every valid pixel")
                dod_data[valid] -= stable_mean
                stable_mean_corrected = 0.0
            else:
                stable_mean_corrected = stable_mean

            valid_dod = dod_data[valid]

            n_total   = cop_data.size
            n_valid   = int(valid.sum())

            if n_valid == 0:
                fail(
                    "DoD has zero valid pixels — Copernicus and NASADEM do not overlap after alignment.",
                    hint=(
                        "Re-run the align_dems task. If it still fails, delete "
                        f"DEM_Downloads/{safe_folder_name}/ and re-run from fetch_bbox."
                    ),
                )

            # Distributional stats describe the raw signal (pre-LoD).
            mean_change   = float(np.mean(valid_dod))
            std_change    = float(np.std(valid_dod))
            min_change    = float(np.min(valid_dod))
            max_change    = float(np.max(valid_dod))
            median_change = float(np.median(valid_dod))

            total_area_m2 = n_valid * pixel_area_m2

            # LoD mask: pixels with |Δz| below the 95% detection limit are
            # noise and must not contribute to gain/loss areas or volumes —
            # without this, quiet sites read as fully disturbed.
            significant       = valid & np.isfinite(dod_data) & (np.abs(dod_data) >= lod_95)
            sig_dod           = dod_data[significant]
            n_gain            = int((sig_dod > 0).sum())
            n_loss            = int((sig_dod < 0).sum())
            n_stable          = n_valid - n_gain - n_loss

            gain_area_m2      = n_gain * pixel_area_m2
            loss_area_m2      = n_loss * pixel_area_m2

            # Volumetric estimates (sum of significant changes × pixel area)
            vol_gain_m3 = float(sig_dod[sig_dod > 0].sum()) * pixel_area_m2
            vol_loss_m3 = float(sig_dod[sig_dod < 0].sum()) * pixel_area_m2
            net_vol_m3  = vol_gain_m3 + vol_loss_m3

            out_meta = cop_src.meta.copy()
            out_meta.update(dtype=rasterio.float32, nodata=np.nan)
            with rasterio.open(dod_path, 'w', **out_meta) as dest:
                dest.write(dod_data, 1)

    except SystemExit:
        raise
    except FileNotFoundError as e:
        fail(
            f"Source raster vanished mid-computation: {e}",
            hint="Another task likely cleaned up the folder. Re-run from fetch_bbox.",
        )
    except MemoryError:
        fail(
            "Out of memory while computing DoD.",
            hint="Reduce the bbox area or give the Airflow worker more RAM.",
        )
    except Exception as e:
        fail(
            f"DoD computation failed ({e.__class__.__name__}: {e})",
            hint="Inspect the input rasters — alignment may be inconsistent.",
        )

    log("-" * 50)
    log(f"DoD statistics for {target_site_name}")
    log(f"  Valid pixels:           {n_valid:,} / {n_total:,} ({n_valid/n_total*100:.1f}%)")
    log(f"  Pixel area:             {pixel_area_m2:.1f} m² ({pixel_area_m2/10000:.4f} ha)")
    log(f"  Mean change:            {mean_change:+.3f} m   (median {median_change:+.3f}, std {std_change:.3f})")
    log(f"  Range:                  {min_change:+.3f} m → {max_change:+.3f} m")
    log(f"  Gain pixels:            {n_gain:,} ({gain_area_m2/10000:.2f} ha)")
    log(f"  Loss pixels:            {n_loss:,} ({loss_area_m2/10000:.2f} ha)")
    log(f"  Volume gain / loss:     {vol_gain_m3:+,.1f} m³ / {vol_loss_m3:+,.1f} m³")
    log(f"  Net volume change:      {net_vol_m3:+,.1f} m³")

    meta["dod_path"] = dod_path
    meta["dod_stats"] = {
        "n_valid_pixels"  : n_valid,
        "pixel_area_m2"   : round(pixel_area_m2, 4),
        "total_area_ha"   : round(total_area_m2 / 10000, 4),
        "mean_change_m"   : round(mean_change, 4),
        "median_change_m" : round(median_change, 4),
        "std_change_m"    : round(std_change, 4),
        "min_change_m"    : round(min_change, 4),
        "max_change_m"    : round(max_change, 4),
        "gain_area_ha"    : round(gain_area_m2 / 10000, 4),
        "loss_area_ha"    : round(loss_area_m2 / 10000, 4),
        "vol_gain_m3"     : round(vol_gain_m3, 2),
        "vol_loss_m3"     : round(vol_loss_m3, 2),
        "net_vol_m3"      : round(net_vol_m3, 2),
        "lod_95_m"        : round(lod_95, 4),
    }
    meta["stable_terrain"] = {
        "n_stable_pixels"     : n_stable_terrain,
        "stable_pct"          : round(stable_pct, 2),
        "stable_mean_m"       : round(stable_mean_corrected, 4),
        "stable_mean_raw_m"   : round(stable_mean, 4),
        "stable_std_m"        : round(stable_std, 4),
        "stable_nmad_m"       : round(stable_nmad, 4),
        "sigma_combined_m"    : round(sigma_combined, 4),
        "t_value"             : t_value,
        "lod_95_m"            : round(lod_95, 4),
    }

    with open(metadata_path, 'w') as f:
        json.dump(meta, f, indent=2)

    log(f"DoD raster saved to {dod_path}")
    log("Generating DoD visualisations (4 images + PDF report)…")

    map_path  = os.path.join(output_dir, f"{safe_folder_name}_DoD_Map.png")
    td3_path  = os.path.join(output_dir, f"{safe_folder_name}_DoD_3D.png")
    sat_path  = os.path.join(output_dir, f"{safe_folder_name}_DoD_Satellite.png")
    hist_path = os.path.join(output_dir, f"{safe_folder_name}_DoD_Histogram.png")

    try:
        log("  [1/4] Plan-view map")
        create_dod_map(dod_path, map_path, meta["dod_stats"])
        log("  [2/4] 3D hillshade overlay")
        create_dod_3d_overlay(dod_path, copernicus_path, td3_path, lod=lod_95)
        log("  [3/4] Satellite context overlay")
        create_dod_context_overlay(dod_path, sat_path, lod=lod_95)
        log("  [4/4] Histogram")
        create_dod_histogram(valid_dod, hist_path, lod=lod_95)
    except Exception as e:
        fail(
            f"Visualisation step failed ({e.__class__.__name__}: {e})",
            hint="The DoD raster was written successfully — re-run this task to regenerate the PNGs.",
        )

    try:
        report_path = generate_dod_report(
            site_name  = target_site_name,
            output_dir = output_dir,
            dod_path   = dod_path,
            stats      = meta["dod_stats"],
        )
        log(f"DoD report saved to {report_path}")
    except Exception as e:
        fail(
            f"DoD report PDF generation failed ({e.__class__.__name__}: {e})",
            hint="Check ReportLab can find its fonts; raster outputs are still on disk.",
        )

    aw3d_path = meta.get("aw3d30_aligned")
    if aw3d_path and os.path.exists(aw3d_path):
        log("Computing multi-period DoDs (AW3D30 available)…")
        from rasterio.warp import reproject, Resampling

        def _compute_dod(modern_p, baseline_p, out_p):
            """Lightweight DoD = modern − baseline. Returns stats dict."""
            with rasterio.open(modern_p) as m, rasterio.open(baseline_p) as b:
                md = m.read(1).astype(np.float32)
                bd = b.read(1).astype(np.float32)
                vld = np.isfinite(md) & (md > -9000) & np.isfinite(bd) & (bd > -9000)
                if m.nodata is not None:
                    vld &= (md != m.nodata)
                if b.nodata is not None:
                    vld &= (bd != b.nodata)
                dd = np.full(md.shape, np.nan, dtype=np.float32)
                dd[vld] = md[vld] - bd[vld]
                rx = abs(m.transform.a); ry = abs(m.transform.e)
                clat = (m.bounds.top + m.bounds.bottom) / 2.0
                pa = (rx * 111320 * np.cos(np.radians(clat))) * (ry * 111320)
                vd = dd[vld]
                st = {
                    "n_valid_pixels": int(vld.sum()),
                    "pixel_area_m2": round(pa, 4),
                    "mean_change_m": round(float(np.mean(vd)), 4),
                    "std_change_m": round(float(np.std(vd)), 4),
                    "min_change_m": round(float(np.min(vd)), 4),
                    "max_change_m": round(float(np.max(vd)), 4),
                    "vol_gain_m3": round(float(vd[vd > 0].sum()) * pa, 2),
                    "vol_loss_m3": round(float(vd[vd < 0].sum()) * pa, 2),
                    "net_vol_m3": round(float(vd.sum()) * pa, 2),
                }
                om = m.meta.copy()
                om.update(dtype=rasterio.float32, nodata=np.nan)
                with rasterio.open(out_p, 'w', **om) as dst:
                    dst.write(dd, 1)
            return st

        dod1_path = os.path.join(output_dir, f"{safe_folder_name}_DoD_Period1_AW3D30_minus_NASADEM.tif")
        dod2_path = os.path.join(output_dir, f"{safe_folder_name}_DoD_Period2_Cop_minus_AW3D30.tif")

        log("  Period 1: AW3D30 − NASADEM (2006-2011 vs 2000)")
        stats1 = _compute_dod(aw3d_path, nasadem_aligned_path, dod1_path)
        log(f"    mean {stats1['mean_change_m']:+.3f} m | net vol {stats1['net_vol_m3']:+,.0f} m³")

        log("  Period 2: Copernicus − AW3D30 (2011-2015 vs 2006-2011)")
        stats2 = _compute_dod(copernicus_path, aw3d_path, dod2_path)
        log(f"    mean {stats2['mean_change_m']:+.3f} m | net vol {stats2['net_vol_m3']:+,.0f} m³")

        # Generate 3-panel comparison map
        mp_png = os.path.join(output_dir, f"{safe_folder_name}_MultiPeriod_DoD.png")
        _create_multiperiod_map(
            [dod1_path, dod2_path, dod_path],
            [
                "Period 1: 2000 → 2006-11\n(AW3D30 − NASADEM)",
                "Period 2: 2006-11 → 2011-15\n(Copernicus − AW3D30)",
                "Full Span: 2000 → 2011-15\n(Copernicus − NASADEM)",
            ],
            mp_png, target_site_name,
        )

        meta["multi_period_dod"] = {
            "period1_path": dod1_path,
            "period1_label": "AW3D30 − NASADEM (2006-2011 vs 2000)",
            "period1_stats": stats1,
            "period2_path": dod2_path,
            "period2_label": "Copernicus − AW3D30 (2011-2015 vs 2006-2011)",
            "period2_stats": stats2,
            "comparison_map": mp_png,
        }
        with open(metadata_path, 'w') as f:
            json.dump(meta, f, indent=2)
        log("Multi-period metadata updated.")
    else:
        log("AW3D30 not aligned! skipping multi-period DoD (this is normal for some sites).")

    log("Next step: vectorization")


if __name__ == "__main__":
    main()
