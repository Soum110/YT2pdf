"""
simulation_generator.py — Programmatic diagram and simulation generator using Matplotlib.

Generates crisp, high-resolution supplementary technical figures when lectures
briefly touch upon topics (e.g., 3D coordinate frames, signal sampling, vector algebra).
"""

import io
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

log = logging.getLogger("simulation_gen")

# Professional styling
plt.rcParams["font.sans-serif"] = ["Helvetica", "Arial", "DejaVu Sans"]
plt.rcParams["axes.edgecolor"] = "#cbd5e1"
plt.rcParams["axes.linewidth"] = 0.8


def generate_3d_coordinates_figure(output_path: Path) -> Path:
    """Generates an exhaustive 3D Cartesian coordinate frame with unit basis vectors and projections."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(7, 6), dpi=220)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_facecolor("#ffffff")

    # Draw coordinate axes
    ax.quiver(0, 0, 0, 1.4, 0, 0, color="#2563eb", arrow_length_ratio=0.08, linewidth=2.2, label="X-axis (i)")
    ax.quiver(0, 0, 0, 0, 1.4, 0, color="#16a34a", arrow_length_ratio=0.08, linewidth=2.2, label="Y-axis (j)")
    ax.quiver(0, 0, 0, 0, 0, 1.4, color="#dc2626", arrow_length_ratio=0.08, linewidth=2.2, label="Z-axis (k)")

    # Unit basis vectors i_hat, j_hat, k_hat
    ax.quiver(0, 0, 0, 0.4, 0, 0, color="#1d4ed8", arrow_length_ratio=0.15, linewidth=3.5)
    ax.quiver(0, 0, 0, 0, 0.4, 0, color="#15803d", arrow_length_ratio=0.15, linewidth=3.5)
    ax.quiver(0, 0, 0, 0, 0, 0.4, color="#b91c1c", arrow_length_ratio=0.15, linewidth=3.5)
    ax.text(0.45, -0.05, -0.05, r"$\hat{i}$", fontsize=12, fontweight="bold", color="#1d4ed8")
    ax.text(-0.05, 0.45, -0.05, r"$\hat{j}$", fontsize=12, fontweight="bold", color="#15803d")
    ax.text(-0.05, -0.05, 0.45, r"$\hat{k}$", fontsize=12, fontweight="bold", color="#b91c1c")

    # Target vector P(x, y, z)
    px, py, pz = 0.85, 0.95, 1.05
    ax.quiver(0, 0, 0, px, py, pz, color="#7c3aed", arrow_length_ratio=0.07, linewidth=3.0)
    ax.scatter([px], [py], [pz], color="#7c3aed", s=70, edgecolors="#4c1d95", linewidth=1.5)
    ax.text(px + 0.04, py + 0.04, pz + 0.05, r"$\vec{v} = x\hat{i} + y\hat{j} + z\hat{k}$", fontsize=12, fontweight="bold", color="#581c87")

    # Orthogonal projection dashed lines
    ax.plot([px, px], [py, py], [0, pz], color="#64748b", linestyle="--", linewidth=1.2)
    ax.plot([px, px], [0, py], [0, 0], color="#64748b", linestyle="--", linewidth=1.2)
    ax.plot([0, px], [py, py], [0, 0], color="#64748b", linestyle="--", linewidth=1.2)
    ax.plot([0, px], [py, py], [pz, pz], color="#94a3b8", linestyle=":", linewidth=1.0)
    ax.scatter([px], [py], [0], color="#64748b", s=30, alpha=0.6)
    ax.text(px + 0.02, py + 0.02, 0.02, r"$(x, y, 0)$ projection", fontsize=8.5, color="#475569")

    # Shaded XY ground plane
    xx, yy = np.meshgrid(np.linspace(0, 1.2, 10), np.linspace(0, 1.2, 10))
    zz = np.zeros_like(xx)
    ax.plot_surface(xx, yy, zz, alpha=0.08, color="#3b82f6")

    ax.set_xlim([0, 1.4])
    ax.set_ylim([0, 1.4])
    ax.set_zlim([0, 1.4])
    ax.set_xlabel("X-Axis (Depth)", fontsize=9.5, fontweight="bold", labelpad=5, color="#1e293b")
    ax.set_ylabel("Y-Axis (Width)", fontsize=9.5, fontweight="bold", labelpad=5, color="#1e293b")
    ax.set_zlabel("Z-Axis (Height)", fontsize=9.5, fontweight="bold", labelpad=5, color="#1e293b")
    ax.view_init(elev=24, azim=40)
    ax.set_title("3D Cartesian Frame: Basis Vectors & Coordinate Projections", fontsize=12, fontweight="bold", color="#0f172a", pad=12)

    plt.tight_layout()
    plt.savefig(str(output_path), bbox_inches="tight", dpi=220)
    plt.close(fig)
    log.info("Generated 3D coordinate frame figure: %s", output_path)
    return output_path


def generate_signal_sampling_figure(output_path: Path) -> Path:
    """Generates a comparison of continuous analog waveform vs discrete digital samples."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5.5), dpi=220, sharex=True)

    t_cont = np.linspace(0, 2, 500)
    f0 = 1.0
    x_cont = np.sin(2 * np.pi * f0 * t_cont) + 0.3 * np.cos(4 * np.pi * f0 * t_cont)

    # Continuous Signal
    ax1.plot(t_cont, x_cont, color="#2563eb", linewidth=2.2, label=r"Continuous Signal $x(t)$")
    ax1.set_ylabel("Amplitude", fontsize=10, fontweight="bold", color="#1e293b")
    ax1.set_title("Analog Domain: Continuous-Time Signal x(t)", fontsize=11, fontweight="bold", color="#0f172a")
    ax1.grid(True, linestyle="--", alpha=0.5)
    ax1.legend(loc="upper right", frameon=True)

    # Sampled Discrete Signal
    fs = 12  # sampling frequency
    t_samp = np.linspace(0, 2, int(2 * fs) + 1)
    x_samp = np.sin(2 * np.pi * f0 * t_samp) + 0.3 * np.cos(4 * np.pi * f0 * t_samp)

    markerline, stemlines, baseline = ax2.stem(t_samp, x_samp, linefmt="#7c3aed", markerfmt="o", basefmt="k-")
    plt.setp(markerline, markersize=5.5, color="#6d28d9")
    plt.setp(stemlines, linewidth=1.6, color="#7c3aed")
    ax2.plot(t_cont, x_cont, color="#94a3b8", linestyle=":", linewidth=1.2, alpha=0.6, label="Original Envelope")
    ax2.set_xlabel("Time (seconds)", fontsize=10, fontweight="bold", color="#1e293b")
    ax2.set_ylabel("Amplitude", fontsize=10, fontweight="bold", color="#1e293b")
    ax2.set_title(r"Digital Domain: Uniform Sampling at $T_s = 1/f_s$ ($x[n] = x(n T_s)$)", fontsize=11, fontweight="bold", color="#0f172a")
    ax2.grid(True, linestyle="--", alpha=0.5)
    ax2.legend(loc="upper right", frameon=True)

    plt.tight_layout()
    plt.savefig(str(output_path), bbox_inches="tight", dpi=220)
    plt.close(fig)
    log.info("Generated signal sampling figure: %s", output_path)
    return output_path


def generate_dot_cross_product_figure(output_path: Path) -> Path:
    """Generates geometric visualization of vector dot and cross products."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6.5, 4.5), dpi=220)
    ax.set_aspect("equal")

    # Vector A along X-axis
    ax.quiver(0, 0, 3.0, 0, angles="xy", scale_units="xy", scale=1, color="#2563eb", width=0.012)
    ax.text(1.5, -0.3, r"$\vec{A}$", fontsize=13, fontweight="bold", color="#1d4ed8")

    # Vector B at angle theta
    theta = np.deg2rad(45)
    bx = 2.4 * np.cos(theta)
    by = 2.4 * np.sin(theta)
    ax.quiver(0, 0, bx, by, angles="xy", scale_units="xy", scale=1, color="#16a34a", width=0.012)
    ax.text(bx / 2 - 0.25, by / 2 + 0.15, r"$\vec{B}$", fontsize=13, fontweight="bold", color="#15803d")

    # Projection of B onto A
    ax.plot([bx, bx], [0, by], color="#64748b", linestyle="--", linewidth=1.4)
    ax.plot([0, bx], [0, 0], color="#dc2626", linewidth=3.0, alpha=0.7)
    ax.text(bx / 2 - 0.2, -0.45, r"$\text{proj}_{\vec{A}}\vec{B} = |\vec{B}|\cos(\theta)$", fontsize=10.5, fontweight="bold", color="#b91c1c")

    # Arc for theta
    arc_t = np.linspace(0, theta, 50)
    ax.plot(0.6 * np.cos(arc_t), 0.6 * np.sin(arc_t), color="#475569", linewidth=1.5)
    ax.text(0.75, 0.2, r"$\theta$", fontsize=12, fontweight="bold", color="#334155")

    ax.set_xlim([-0.5, 3.5])
    ax.set_ylim([-0.8, 2.2])
    ax.axis("off")
    ax.set_title(r"Dot Product Projection: $\vec{A}\cdot\vec{B} = |\vec{A}||\vec{B}|\cos(\theta)$", fontsize=11.5, fontweight="bold", pad=10)

    plt.tight_layout()
    plt.savefig(str(output_path), bbox_inches="tight", dpi=220)
    plt.close(fig)
    log.info("Generated dot product figure: %s", output_path)
    return output_path


def execute_custom_simulation_code(code_str: str, output_path: Path) -> Optional[Path]:
    """Safely executes AI-generated matplotlib code to generate a custom diagram."""
    if not code_str or not code_str.strip():
        return None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Clean code formatting
    clean_code = code_str.strip()
    if clean_code.startswith("```"):
        lines = clean_code.splitlines()
        clean_code = "\n".join(l for l in lines if not l.startswith("```"))

    # Fix accidental \v vertical tab or escaped LaTeX commands
    clean_code = clean_code.replace("\x0b", r"\v").replace("\\vec", r"\vec")

    exec_globals = {
        "plt": plt,
        "np": np,
        "output_path": str(output_path),
    }

    try:
        # Append savefig if not present
        if "savefig" not in clean_code:
            clean_code += f"\nplt.savefig(r'{str(output_path)}', bbox_inches='tight', dpi=200)\nplt.close('all')"
        exec(clean_code, exec_globals)
        if output_path.exists() and output_path.stat().st_size > 2000:
            log.info("Custom simulation figure generated successfully: %s", output_path)
            return output_path
    except Exception as e:
        log.warning("Custom simulation code execution failed: %s", e)
    return None
