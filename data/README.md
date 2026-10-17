# Dashcam samples

The real road videos come from the official [Udacity CarND Vehicle Detection](https://github.com/udacity/CarND-Vehicle-Detection)
repository. The upstream repository carries an MIT license, retained in
`UDACITY_LICENSE.txt`. Videos are downloaded locally and are not checked into Git.

Run `python tools/prepare_dashcam.py` to download both videos, or add `--short-only`
for the small 1.52-second fixture. Source URLs, attribution and default normalized
road regions are recorded in `sources.json`. The download helper records a SHA256
and source receipt next to each video.

The helper also creates a browser-ready 12-second excerpt (28–40 seconds) of the
original 50.4-second highway drive. It is resized to 960×540 and resampled to 15 FPS
without changing playback speed. This curated clip is the dashboard default.

These are actual dashcam recordings, not synthetic footage. They provide visual
detection/tracking tests; they do **not** provide synchronized reference TTC,
distance or closing-speed annotations. A recorded drive need not trigger an alert.
Use the separately labeled synthetic approach clip to demonstrate known warning levels.
