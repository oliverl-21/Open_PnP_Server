# Image install

Image install lets a device download a mapped image and see a reload request before any config is offered.

## Sub-features

- `image-offer` offers `urn:cisco:pnp:image-install` when `image_map/<serial>.txt` names a file in the image directory.
- `image-download` serves those image bytes.
- `image-reload` includes a reload element on the image offer.
- `image-next` waits for a config after the image response, when no config file exists.

## How to get to it (user POV)

- The operator writes an image file and a one-line map named for the serial.
- The device answers device-info, then receives the image offer and downloads it.
- The device answers the image job and, with no config file yet, is told to call back.

## Driving it with pnp-verify

Preconditions:

- `pnp-verify doctor --run "$RUN"` prints `doctor ok`.
- This run has no config file and no image map yet.
- `GET /status` is `[]`.

- **Place the image.** Run `pnp-verify seed-image --run "$RUN" --name ios.bin --text "image-bytes" --map FCW1234.txt`. Stdout names the image path and the map path.
- **Answer device-info.** Run `pnp-verify work-request --run "$RUN" --correlator C1 --save c1-device-info.xml`, then `pnp-verify work-response --run "$RUN" --correlator C1 --xmlns urn:cisco:pnp:device-info --success 1 --hostname sw1 --platform C9300-24P --version 17.9.4 --save c1-bye.xml`. The second summary has `bye=yes`.
- **Poll for the image.** Run `pnp-verify work-request --run "$RUN" --correlator C2 --save c2-image.xml`. The summary has `xmlns=urn:cisco:pnp:image-install`, `reload=yes`, and `location` equal to the launch `base` plus `/images/ios.bin`.
- **Download the image.** Run `pnp-verify download --run "$RUN" --path /images/ios.bin --save image-body.txt`. The summary has `http=200` and `text=image-bytes`.
- **Accept the image.** Run `pnp-verify work-response --run "$RUN" --correlator C2 --xmlns urn:cisco:pnp:image-install --success 1 --save c2-bye.xml`. The summary has `bye=yes` and `correlator=C2`.
- **Poll with no config.** Run `pnp-verify work-request --run "$RUN" --correlator C3 --save c3-backoff.xml`. The summary has `callback=yes`, `terminate=no`, and `reason=no config for FCW1234`.
- **Proof.** `c2-image.xml` contains the image location and a reload element. `image-body.txt` is `image-bytes`. `c3-backoff.xml` contains `callbackAfter`.

## Gotchas

- The map file is `<serial>.txt` and its first non-comment line is the image filename. A map that names a missing file produces backoff instead of an image offer.
- Image install is offered before config. Seeding a config does not skip the image when the map exists.
- The image offer always includes reload. `noreload=yes` on this response means the server offered config, not image.
