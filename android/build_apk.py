"""Automated Android APK Builder for RemoteDesk Pro (dist/RemoteDeskPro-Android.apk).
Compiles the native Android source code (`android/app/src/main/java/com/remotedesk/pro/*.java`),
resources, and assets using the installed Android SDK (`aapt2`, `d8`, `zipalign`, `apksigner`)
and JDK (`javac`, `keytool`) into a signed, installable Android APK.
"""

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw


def find_sdk_and_jdk():
    """Locate Android SDK build-tools, platform android.jar, and JDK tools on Windows."""
    local_appdata = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    sdk_root = Path(os.environ.get("ANDROID_HOME", str(Path(local_appdata) / "Android" / "Sdk")))

    # Find latest build-tools
    bt_root = sdk_root / "build-tools"
    bt_dirs = sorted([d for d in bt_root.iterdir() if d.is_dir()], reverse=True)
    if not bt_dirs:
        raise FileNotFoundError(f"No Android build-tools found in {bt_root}")
    build_tools = bt_dirs[0]

    # Find android.jar (prefer android-35 or android-36)
    plat_root = sdk_root / "platforms"
    android_jar = None
    for pref in ("android-35", "android-36", "android-34"):
        candidate = plat_root / pref / "android.jar"
        if candidate.exists():
            android_jar = candidate
            break
    if not android_jar:
        jars = list(plat_root.glob("*/android.jar"))
        if not jars:
            raise FileNotFoundError(f"No android.jar found in {plat_root}")
        android_jar = jars[0]

    # Find javac and keytool
    javac_exe = shutil.which("javac")
    keytool_exe = shutil.which("keytool")
    if not javac_exe:
        raise FileNotFoundError("javac not found in PATH")

    return {
        "build_tools": build_tools,
        "aapt2": build_tools / "aapt2.exe",
        "d8": build_tools / "d8.bat",
        "zipalign": build_tools / "zipalign.exe",
        "apksigner": build_tools / "apksigner.bat",
        "android_jar": android_jar,
        "javac": Path(javac_exe),
        "keytool": Path(keytool_exe) if keytool_exe else Path(javac_exe).parent / "keytool.exe",
    }


def generate_android_launcher_icon(target_png: Path, size: int = 192) -> None:
    """Generate a high-resolution squircle launcher icon for the Android APK."""
    target_png.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Squircle background
    draw.rounded_rectangle([(8, 8), (size - 8, size - 8)], radius=42, fill=(10, 22, 46, 255), outline=(0, 212, 255, 255), width=6)
    # Inner monitor bezel
    draw.rounded_rectangle([(34, 40), (size - 34, size - 68)], radius=12, fill=(0, 212, 255, 255))
    draw.rounded_rectangle([(42, 48), (size - 42, size - 76)], radius=6, fill=(14, 30, 60, 255))
    # Stand
    cx = size // 2
    draw.rectangle([(cx - 10, size - 68), (cx + 10, size - 42)], fill=(240, 246, 252, 255))
    draw.rounded_rectangle([(cx - 34, size - 44), (cx + 34, size - 34)], radius=5, fill=(0, 212, 255, 255))
    img.save(target_png, format="PNG")


def build_apk() -> Path:
    """Compile and sign dist/RemoteDeskPro-Android.apk."""
    root_dir = Path(__file__).resolve().parent.parent
    android_dir = root_dir / "android"
    main_dir = android_dir / "app" / "src" / "main"
    build_dir = root_dir / "build" / "android_apk"
    dist_dir = root_dir / "dist"

    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir.mkdir(parents=True, exist_ok=True)

    tools = find_sdk_and_jdk()
    print(f"[*] Using Android SDK Platform: {tools['android_jar'].parent.name}")
    print(f"[*] Using Build-Tools: {tools['build_tools'].name}")

    # 1. Generate drawable/ic_launcher.png
    icon_png = main_dir / "res" / "drawable" / "ic_launcher.png"
    generate_android_launcher_icon(icon_png)

    # 2. Compile resources with aapt2
    compiled_res_zip = build_dir / "resources.zip"
    subprocess.run(
        [
            str(tools["aapt2"]),
            "compile",
            "--dir",
            str(main_dir / "res"),
            "-o",
            str(compiled_res_zip),
        ],
        check=True,
    )

    # 3. Link resources, AndroidManifest.xml, and assets with aapt2
    unaligned_apk = build_dir / "unaligned.apk"
    r_java_dir = build_dir / "gen"
    r_java_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            str(tools["aapt2"]),
            "link",
            "-o",
            str(unaligned_apk),
            "-I",
            str(tools["android_jar"]),
            "--manifest",
            str(main_dir / "AndroidManifest.xml"),
            "-A",
            str(main_dir / "assets"),
            "--java",
            str(r_java_dir),
            "--auto-add-overlay",
            str(compiled_res_zip),
        ],
        check=True,
    )

    # 4. Compile Java sources + generated R.java with javac (Targeting Java 11 bytecode for d8)
    classes_dir = build_dir / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)

    java_files = list((main_dir / "java").rglob("*.java")) + list(r_java_dir.rglob("*.java"))
    javac_cmd = [
        str(tools["javac"]),
        "--release",
        "11",
        "-encoding",
        "UTF-8",
        "-cp",
        str(tools["android_jar"]),
        "-d",
        str(classes_dir),
    ] + [str(f) for f in java_files]

    subprocess.run(javac_cmd, check=True)

    # 5. Convert .class files into classes.dex using d8
    dex_dir = build_dir / "dex"
    dex_dir.mkdir(parents=True, exist_ok=True)
    class_files = list(classes_dir.rglob("*.class"))
    d8_cmd = [
        str(tools["d8"]),
        "--lib",
        str(tools["android_jar"]),
        "--min-api",
        "26",
        "--output",
        str(dex_dir),
    ] + [str(f) for f in class_files]

    subprocess.run(d8_cmd, check=True)

    # 6. Inject classes.dex into unaligned.apk
    classes_dex = dex_dir / "classes.dex"
    with zipfile.ZipFile(unaligned_apk, "a", zipfile.ZIP_DEFLATED) as zf:
        zf.write(classes_dex, arcname="classes.dex")

    # 7. Align APK with zipalign
    aligned_apk = build_dir / "aligned.apk"
    subprocess.run(
        [
            str(tools["zipalign"]),
            "-p",
            "-f",
            "4",
            str(unaligned_apk),
            str(aligned_apk),
        ],
        check=True,
    )

    # 8. Generate signing keystore if not present and sign APK with apksigner
    keystore = build_dir / "release.keystore"
    subprocess.run(
        [
            str(tools["keytool"]),
            "-genkeypair",
            "-v",
            "-keystore",
            str(keystore),
            "-alias",
            "remotedesk",
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-storepass",
            "remotedesk123",
            "-keypass",
            "remotedesk123",
            "-dname",
            "CN=RemoteDesk Pro, OU=Mobile, O=RemoteDesk, L=City, S=State, C=US",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    final_apk = dist_dir / "RemoteDeskPro-Android.apk"
    subprocess.run(
        [
            str(tools["apksigner"]),
            "sign",
            "--ks",
            str(keystore),
            "--ks-key-alias",
            "remotedesk",
            "--ks-pass",
            "pass:remotedesk123",
            "--key-pass",
            "pass:remotedesk123",
            "--out",
            str(final_apk),
            str(aligned_apk),
        ],
        check=True,
    )

    # 9. Verify APK signature and copy to assets/ for Windows Host bundling
    subprocess.run([str(tools["apksigner"]), "verify", str(final_apk)], check=True)
    assets_dir = root_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_apk, assets_dir / "RemoteDeskPro-Android.apk")

    size_kb = final_apk.stat().st_size / 1024
    print(f"[SUCCESS] Built & signed Android APK: {final_apk} ({size_kb:.1f} KB)")
    return final_apk


if __name__ == "__main__":
    build_apk()
