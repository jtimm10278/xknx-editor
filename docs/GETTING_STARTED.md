# Getting Started

A plain-English walk-through for a first session with XKNX Editor: install it, open your project
without touching your installation, look around, and only then program a device, starting with a
spare one.

## 1. Install

**macOS** (Apple silicon or Intel):

1. Download `XKNX-Editor-macos.zip` from the [latest release](https://github.com/XKNX/xknx-editor/releases/latest)
   and double-click it. You get `XKNX-Editor.app`.
2. Drag `XKNX-Editor.app` into **Applications**.
3. The app is not signed by Apple yet, so macOS blocks the first start ("cannot be opened" or
   "damaged"). Either
   - open **System Settings > Privacy & Security**, scroll down to the message about XKNX-Editor
     and click **Open Anyway**, then **Open**; or
   - in Terminal run
     `xattr -dr com.apple.quarantine "/Applications/XKNX-Editor.app"`
     and start the app again.

   This is only needed once per downloaded version.

**Windows**: run the installer from the release (`XKNX-Editor-windows-installer-x64.exe`, or the
arm64 one on Windows on ARM). **Linux**: use the AppImage or the `tar.gz`.

To build the Mac app yourself instead, see [Building the app yourself](#building-the-app-yourself).

## 2. Start offline

At start the editor looks for KNX interfaces and connects to the one it used last. For a first
look, keep it away from your bus:

- start it without connecting: `"/Applications/XKNX-Editor.app/Contents/MacOS/XKNX-Editor" --no-connect`
  (Windows: `XKNX-Editor.exe --no-connect`), and
- in **File > Settings… > Connection**, switch **Connect at Startup** off, so later starts stay
  offline too until you choose an interface yourself.

If your version has **Block Bus Writes** (also under **Settings > Connection**), switch it on as
well: the editor can then read from the bus but changes nothing there.

## 3. Open your project

**File > Open Project (.knxproj/.xknx)…** and pick your ETS project export (`.knxproj`). The
editor imports it into a new XKNX project file (`.xknx`) next to it; your `.knxproj` is only read.
If the project has a password, the editor asks for it.

Work on a copy if you like to be extra careful: copy the `.knxproj` first and open the copy.

Now look around: the topology and group address trees on the left, a device's parameters and
group objects in the middle, the **Health** panel for things worth checking. Every change can be
undone (**Edit > Undo**, or the History panel). Nothing you do here reaches a device until you
program it.

## 4. Check before you write

Before programming anything:

- **Simulate first**, where your version offers **Simulate Download**: it runs the whole download
  offline and lists every frame it would send and every byte it would change.
- With a bus connection, **Test Programming** reads the device back (read-only) and shows the
  exact changes a download would make, without writing anything.
- Check the device's **Tested** badge where your version shows one: it tells you whether someone
  has already proven that application with the editor.

## 5. A first download, on a spare device

Make your first real download on a device that does nothing important, for example a spare push
button interface on a test lead, not the switch by your front door:

1. Keep a backup of the project (**File > Backups**) and, for ETS projects, your own ETS backup.
2. Connect to your interface (**Settings > Connection**, or the connection menu), and switch
   **Block Bus Writes** off only now, if you use it.
3. Give the spare device its individual address: put it in programming mode (its button) and use
   the address download.
4. Select the device, choose the download scope (**Full** the first time) and press
   **Program Device**. Watch the progress, then check the device does what you set.
5. Only after that, move on to devices in use, one at a time.

If something goes wrong, stop, and report it with **Help > Report a Bug**, attaching the log of that
run (**Settings > Folders > Log Folder**; on macOS `~/Library/Logs/xknx-editor`).

## Building the app yourself

On a Mac with [uv](https://docs.astral.sh/uv/) installed:

```bash
git clone https://github.com/XKNX/xknx-editor.git
cd xknx-editor
uv sync --all-packages
apps/editor-gui/tools/build_desktop.sh --zip
```

The app lands in `apps/editor-gui/dist/XKNX-Editor.app`, built for your Mac's own architecture
(arm64 on Apple silicon), plus `XKNX-Editor-macos-<arch>.zip`. It carries an ad-hoc signature only
(no Apple Developer ID), which the script checks. An app you built on your own Mac starts
without the Gatekeeper step; a copy moved to another Mac needs it (step 1).
