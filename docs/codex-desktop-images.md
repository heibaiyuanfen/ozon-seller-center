# Codex Desktop Image Tasks

Select `Codex 桌面任务` in the image-service settings and save the settings
before adding listing jobs. The copy-task button copies a batch instruction.
Send it in Codex Desktop on the same computer after the listing has reached
the image-generation stage. Codex must have access to the task directory and
an image-generation tool.

Each task lives under the application's data directory in
`codex-image-tasks/<task_id>/`. It contains `request.json`, one copied product
reference, and `Codex任务.txt`. Requests include the prompt, offer ID, shop ID,
and shop name. They do not include API credentials.

The generator saves `result.png` in this directory, then writes `result.json`
with the corresponding `task_id` and `status: completed`. Writing the JSON
after the image finishes prevents the application from reading partial files.
An unsuccessful task uses `status: failed` and an `error` string instead.

The application polls once per second for up to 30 minutes. It validates task
identity, file decoding, a 3:4 aspect ratio, and a minimum short side of 600 px.
Only a valid result continues to the existing watermark, distinct-shop-cover
validation, OSS upload, and listing stages. Failure pauses the listing;
desktop-mode failures never silently fall back to the original cover.

Stopping the listing stops the wait. Task files remain available for retry;
an identical request uses the same task ID. Different shops and prompts use
different IDs. Images and reference files must remain on the same computer.

This mode is a local file handoff. It does not automatically open or send a
message to Codex Desktop. One instruction can process the currently exported
batch. Additional tasks exported later require another instruction unless
the Codex task is explicitly asked to keep watching the directory.

The existing image API mode remains the default. Desktop handoff does not
replace the separate copywriting or product-vision API configurations.
