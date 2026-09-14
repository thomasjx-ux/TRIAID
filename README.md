# TRIAID

End-to-end expected-minimax limits for calibrated cold-start prediction from sparse low-rank histories.

## Result

Under the frozen low-rank scaling
$N\asymp A$, fixed $K,\mu,\kappa=O(1)$, $pN\ge\log^3N$, and constant-fraction
target calibration, the expected minimax clipped-normalized prediction risk
satisfies

$$E^\star(\psi,\gamma)\asymp\min\{1,\psi^{-1}+\gamma^{-1}\},$$

above the $O(N^{-3})$ technical floor, where

$$\Psi=\frac{p\lambda_K^2}{\sigma^2(N+A)},\qquad
\Gamma=\frac{C_\star^{\rm eff}\lVert\theta_\star\rVert^2}{K\sigma^2},\qquad
C_\star^{\rm eff}=C_\star(1-h_0).$$

The probability levels are intentionally asymmetric. The state-side term is
controlled with high probability over the reference system, while the
fixed-$K$ response-noise term is controlled in expectation with an explicit
sub-exponential tail. The headline theorem is therefore an expected-minimax
result, not a uniform high-probability minimax guarantee.

## Authorship and accountability

This public preprint is released under the collective authorship `TRIAID Research Team`, with the declared affiliation `Shanghai Pudong Open Computing Innovation Center` and correspondence address `triaidteam@spocic.ac.cn`.

The repository does not provide independent public verification of that affiliation and does not present the institution name, domain suffix, internal audit notes, or automated checks as accreditation, certification, or peer review. Author-side verification files are labeled as such. See `AUTHORS.md` for the accountability statement.

## What is and is not claimed

- For $K=1$, the augmented reference-plus-target data are rank one. We provide an explicit algebraic specialization of $(\Psi,\Gamma)$ in the paper.
- Chen--Xi--Yu study arbitrary sampling patterns. Their rank-one result is path-based, while effective resistance is the sharp characterization for their additive-matrix subclass. We therefore do not claim an identity between effective resistance and $(\Psi,\Gamma)$.
- The contribution here is the fixed-general-rank, two-channel expected-minimax law for the specified random reference/calibration experiment.
- Figures 1, 2, 4, and 5 are diagnostics. They are not substitutes for the proof. In particular, Figures 1 and 2 use a joint synthetic hypothesis family rather than the exact final state-side Fano family.
- In the low-$\Psi$ region the upper proof uses the trivial projector/loss ceiling. Sharpness there comes from the lower bound.

## Repository layout

```text
main.tex, appendix.tex, macros.tex, references.bib   paper source
TRIAID_v1.0.0.pdf                                   compiled preprint
figures/                                             paper figures
data/                                                numerical outputs
experiments/                                         reproduction scripts
tests/                                               static and smoke checks
docs/                                                theorem and author-side verification notes
.github/workflows/ci.yml                             automated checks
```

## Reproduce

```bash
pip install -r requirements.lock
python experiments/fano_full_run.py
python experiments/fano_allocation.py
python experiments/gen_figures.py
python experiments/gen_minimax_surface.py
python experiments/gen_minimax_collapse.py
python experiments/plot_minimax_collapse.py
python experiments/gen_cross_k_collapse.py
python experiments/plot_cross_k_collapse.py
python tests/static_checks.py
python tests/numerical_tolerance_checks.py

# portable one-command reproduction
python reproduce.py
```

See `experiments/README.md` for the figure-to-script mapping, `docs/CONSTANT_BOOKKEEPING.md` for the finite-N constant scope, and `docs/CHANGELOG.md` for the consolidated release-hardening history.

## Release checksums and numerical tolerance

`CHECKSUMS.sha256` is an integrity manifest for the exact files in the published
release snapshot. It is not a promise that rerunning floating-point linear algebra
will reproduce stochastic JSON files bit-for-bit on every NumPy/BLAS build.
Last-bit variation from SVD/QR routines is expected across compatible environments.

The canonical environment is pinned in `requirements.lock`. If those exact wheels
are unavailable, `requirements.txt` allows a compatible NumPy/Pillow range. After
regeneration, validate the two linear-algebra-sensitive datasets with

```bash
python tests/numerical_tolerance_checks.py
```

which compares every field against frozen reference snapshots and uses
`rtol=1e-12, atol=1e-12` for floating-point values while requiring exact structure,
integer values, and strings. Use `sha256sum -c CHECKSUMS.sha256` or `make integrity` only before
modifying or regenerating the release snapshot.

## Compile the paper

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex

# or
python reproduce.py --pdf
```

## Citation

See `CITATION.cff`.

## License

See `LICENSE`.
