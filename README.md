# Tidy

**Tidy** is a lightweight, interactive command-line utility that renames and organizes your files based on plain-language instructions. It requires **Python 3.8+** and uses only the Python standard library with zero external dependencies.

> **GitHub Description:** A zero-dependency Python CLI tool that renames and organizes files using natural language instructions and your choice of AI model (OpenAI, Anthropic, OpenRouter, or local Ollama).

---

## Features

* **Natural Language Organization:** Tell Tidy how to sort, rename, or restructure your files in plain English (e.g., *"put PDFs in Documents, images in Pictures, everything else in Misc"*).
* **Zero Dependencies:** Written entirely with Python's standard library—no `pip install` or virtual environment required.
* **Multi-Provider AI Support:** Built-in presets for **OpenAI**, **Anthropic**, **OpenRouter**, **Ollama** (local models), and any **Custom** OpenAI-compatible endpoint.
* **Privacy First:** Only file relative paths, sizes, and modification dates (`YYYY-MM-DD`) are sent to the AI model—file contents are never read or transmitted.
* **Interactive Dry-Run Previews:** Every instruction generates a numbered preview of proposed moves before touching your disk. You can approve the batch, exclude specific files (`except 2,5`), or type follow-up tweaks to refine the plan.
* **Built-In Safety & Collision Detection:** Automatically blocks path traversal (`..` or targets outside the working folder), flags duplicate target names, prevents overwriting existing files, and ignores hidden dot-files (`.`).
* **Instant Undo:** Revert your last applied batch of changes with `/undo`, which restores files to their original paths and automatically cleans up any empty folders created during the move.

---

## Quick Start

### 1. Download
Download `tidy.py` directly into your project or somewhere on your system `PATH`:

```bash
curl -O https://raw.githubusercontent.com/<your-username>/tidy/main/tidy.py
chmod +x tidy.py
```

### 2. Run
Pass a target directory, or run it without arguments to organize the current folder:

```bash
python3 tidy.py ~/Downloads
```

On first launch, Tidy walks you through an interactive setup to select your AI provider, base URL, model name, and API key. Your preferences are saved to `~/.tidy.json` with restricted `0600` permissions.

---

## Interactive Workflow

Once inside the `tidy>` prompt, describe what you want to do in plain language:

```text
Tidy - tell it how to rename and arrange your files.  /help for tips.
Folder: /Users/alex/Downloads   Model: claude-sonnet-5-5 (anthropic)
Only file names, sizes and dates are sent to the model, never file contents.

tidy> put PDFs in Documents, images in Pictures, and lowercase all filenames
  asking the model… (4/4 files)

✓ 1  IMG_9021.JPG          →  Pictures/img_9021.jpg
✓ 2  Invoice_March.PDF     →  Documents/invoice_march.pdf
✓ 3  receipt scan.png      →  Pictures/receipt scan.png
✗ 4  report.pdf            →  Documents/report.pdf   (a file with this name already exists)

3 of 4 changes selected.
Apply? [y]es / [n]o / except N,N / or type a tweak:
```

### Responding to a Preview
After Tidy displays the proposed plan, you have four options:
1. **Apply (`y` or `yes`):** Executes all selected (`✓`) file moves and renames.
2. **Cancel (`n`, `no`, or `Enter`):** Aborts the plan without changing any files.
3. **Skip Specific Items (`except N,N`):** Type `except 1,3` to uncheck specific items by their line number and review the updated list before applying.
4. **Refine with a Tweak:** Type any additional instruction (e.g., `replace spaces with dashes too`) to append it to your prompt and generate a fresh plan.

### Example Prompts
* `rename photos to YYYY-MM-DD_description using the modified date`
* `put PDFs in Documents, images in Pictures, everything else in Misc`
* `replace spaces with dashes and lowercase everything`

---

## CLI Commands

Use slash commands inside the interactive prompt to manage your session:

| Command | Description |
| :--- | :--- |
| `/folder <path>` | Switch the active working directory (clears the current undo history). |
| `/recursive on\|off` | Toggle whether files inside subdirectories are included (defaults to `off`). |
| `/files` | List all non-hidden files currently visible to Tidy in the target folder. |
| `/undo` | Revert the most recent batch of file moves and prune empty folders. |
| `/config` | Re-run the interactive AI provider and model setup wizard. |
| `/help` | Display usage examples and available slash commands. |
| `/quit` | Exit Tidy (aliases: `/exit`, `/q`, `Ctrl+C`, `Ctrl+D`). |

---

## Configuration & Environment Variables

Tidy stores its configuration in `~/.tidy.json`, which you can override at any time using environment variables:

| Variable | Description |
| :--- | :--- |
| `TIDY_PROVIDER` | Provider preset: `openai`, `anthropic`, `openrouter`, `ollama`, or `custom`. |
| `TIDY_MODEL` | Model identifier (e.g., `gpt-4o`, `claude-sonnet-5-5`, `llama3`). |
| `TIDY_URL` | Override the default API base URL for the selected provider. |
| `TIDY_KEY` | Universal API key override (takes precedence over provider-specific keys). |
| `OPENAI_API_KEY` | Default API key used when provider is set to `openai`. |
| `ANTHROPIC_API_KEY` | Default API key used when provider is set to `anthropic`. |
| `OPENROUTER_API_KEY` | Default API key used when provider is set to `openrouter`. |

### Supported Provider Presets
* **`openai`:** Uses `https://api.openai.com/v1` and `$OPENAI_API_KEY`.
* **`anthropic`:** Uses `https://api.anthropic.com` and `$ANTHROPIC_API_KEY` (defaults to `claude-sonnet-5-5`).
* **`openrouter`:** Uses `https://openrouter.ai/api/v1` and `$OPENROUTER_API_KEY`.
* **`ollama`:** Uses `http://localhost:11434/v1` with no API key required.
* **`custom`:** Uses any custom OpenAI-compatible `/chat/completions` base URL and `$TIDY_KEY`.

---

## Safety & Privacy

* **Metadata Only:** Tidy never opens or reads your files; it only sends `relative/path.ext | size | YYYY-MM-DD` in batches of up to 200 files per request.
* **Dotfile Protection:** Hidden files and hidden directories (anything starting with `.`) are automatically ignored during scanning.
* **Path Jail:** All destination paths are resolved and verified to remain strictly inside the target root folder, blocking `..` traversal or absolute path escapes.
* **No Silent Overwrites:** If two files are assigned the same destination name, or if a target file already exists and isn't being moved, Tidy automatically unchecks (`✗`) the conflicting item and displays a warning note in the preview.

---

## License

This project is licensed under the [MIT License](LICENSE).