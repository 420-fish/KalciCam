# KalciCam

<p align="center">
  <img src="https://github.com/420-fish/KalciCam/releases/download/pictures/kalcicam-flying.png" width="420" alt="">
</p>

KalciCam is a screen recorder for Linux. It records your screen, your game, or just your sound, and saves it as a video you can share.

It uses your graphics card to do the hard work, so your computer stays fast while you record.

---

## Download and start

1. Go to the **[Releases page](../../releases/latest)**.
2. Download **KalciCam-x86_64.AppImage**.
3. Put the file somewhere you'll keep it, for example a folder called **Applications** in your home folder.
4. Right-click the file, open **Properties**, and turn on **"Allow executing file as program"**.
5. Double-click it.

The first time, a setup helper opens. Just follow it. It will:

- let you pick a look
- check that your computer has everything it needs, and install anything missing (it asks for your password)
- test your graphics card
- ask where to save your videos
- add KalciCam to your app menu

---

## Recording

Press the big **REC** button, or press **F12**.
A 3-2-1 countdown shows, then it starts recording. Press it again to stop. Your video is saved right away.
(Don't want the countdown? Turn it off in **Video**.)

| Key | What it does |
|---|---|
| **F12** | Start or stop recording |
| **Shift + F12** | Pause or keep going |
| **F11** | Take a screenshot |
| **F10** | Save the last few seconds (replay) |

You can change these keys in **Extras**.

While you record, the KalciCam icon in your panel turns into a **red dot**. It turns **yellow** when paused. Put your mouse on it to see the time.

---

## What you can record

Click the mode button at the top (it says **Fullscreen** at first) to pick:

- **Select a recording area**: drag a box over the part of the screen you want.
- **Rectangle on a screen**: pick a size (like 1280 x 720), then click to place the box.
- **Fullscreen**: the whole screen.
- **Specific window**: just one window.
- **Around mouse**: a box that follows your mouse around.
- **Audio only**: just sound, no video. Saved as M4A or MP3.

The line under the buttons always shows exactly what will be recorded.

**Game button:** one click sets everything up for games: full screen, smooth 60 fps, game sound on, mouse hidden, and replay ready.

---

## Sound

The **speaker** and **microphone** buttons at the top turn sound on and off.
The little bars next to them move when sound is coming in.

You can pick different speakers or mics and change the volume in **Audio**.

---

## Make the window small

Click the small arrow in the top-right corner. KalciCam shrinks to just the buttons.
Click it again to make it big. KalciCam remembers which size you used last.

---

## Other things

- **Replay** (in **Extras**): keeps the last 30 seconds ready. Press **F10** to save them after something cool happens.
- **Webcam bubble** (in **Overlays**): shows your webcam in a circle you can drag around.
- **Themes**: pick one of five pictures for the background.
- **Recordings**: see, play, and delete your videos.

Videos are saved in your **Videos** folder, inside **KalciCam**.

---

## Problems?

**It says it's recording with the CPU.**
That means it can't use your graphics card yet. Open **About**, click **Run setup again**, and follow the graphics card step. It tells you what to install.

**No sound in my video.**
Check that the speaker or microphone button at the top is on, and that the little bars move when there's sound.

**The keys (F12 and so on) don't work.**
KalciCam shows a red message on the Home page if its keys aren't working. Click **Fix it**, and it lets your desktop handle the keys instead (works on Linux Mint and GNOME). You can also turn this on in **Extras > Hotkeys**.

**"Around mouse" is greyed out.**
It only works on Linux desktops like Linux Mint (called "X11"), not on newer "Wayland" desktops.

**Something else is wrong.**
Open a terminal in the folder with the file and run:

```
./KalciCam-x86_64.AppImage --selftest
```

It records for a few seconds and shows what works and what doesn't. Send that text to whoever gave you KalciCam.

---

## Getting a new version

Download the new file from the **[Releases page](../../releases/latest)**, delete the old one, and do step 4 again. Your settings stay the same.

<p align="center">
  <img src="https://github.com/420-fish/KalciCam/releases/download/pictures/kalcicam-dancing.png" width="260" alt="">
</p>
