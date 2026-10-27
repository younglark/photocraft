# Photoshop probes (ps-probe)

Small 256×256 documents, one parameter varied per probe, for fitting Photoshop's behaviour:
clipping-group blend flags, Hue/Saturation, gradients, patterns, shape/stroke antialiasing, and a
few effects. Same runner and interface as the ps-oracle bundle.

    cd ps-probe && ./run-photoshop.sh . ./probe-out
    tar -czf probe-out.tar.gz probe-out          # send this back

Per probe Photoshop writes `probe-out/<id>/canvas.png` (flattened, 8-bit) and
`probe-out/<id>/resaved.psd` (its own layered PSD of the same document). Track-J probes
(`psd: null` in cases.json) are built inside Photoshop from action descriptors; a failed
build step is recorded in results.json and the run continues.
Expect roughly 10–20 minutes. Do not use Photoshop while it runs.
