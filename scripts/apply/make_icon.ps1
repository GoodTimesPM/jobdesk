# Draw apply.ico - a radar scope, which is what this program actually does:
# sweep the market and mark the contacts worth applying to.
#
# Everything is vector-drawn per size (supersampled 4x, then downsampled), so
# the 16px favicon-sized frame is not a mushy shrink of the 256px one. Detail
# drops out below 32px on purpose.
#
# Keep this file pure ASCII - same BOM/ANSI lesson as install_shortcut.ps1.
#
# Run:  powershell -ExecutionPolicy Bypass -File scripts\make_icon.ps1

Add-Type -AssemblyName System.Drawing

$out = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "apply.ico"

function New-RadarBitmap([int]$size) {
    $ss = 4
    $s  = $size * $ss
    $bmp = New-Object System.Drawing.Bitmap($s, $s, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)

    # Detail budget: below ~32px the crosshair and inner ring just turn to mud.
    $detail = $size -ge 32
    $mid    = $size -ge 24

    $c = $s / 2.0
    $R = $s * 0.46          # outer edge of the bezel
    $F = $R * 0.86          # radius of the scope face (inside the bezel)

    $faceRect = New-Object System.Drawing.RectangleF(($c-$F), ($c-$F), (2*$F), (2*$F))

    # --- scope face -------------------------------------------------------
    $facePath = New-Object System.Drawing.Drawing2D.GraphicsPath
    $facePath.AddEllipse($faceRect)
    $faceBrush = New-Object System.Drawing.Drawing2D.PathGradientBrush($facePath)
    $faceBrush.CenterColor    = [System.Drawing.Color]::FromArgb(255, 10, 46, 33)
    $faceBrush.SurroundColors = @([System.Drawing.Color]::FromArgb(255, 3, 16, 12))
    $g.FillEllipse($faceBrush, $faceRect)

    # everything from here stays inside the face
    $g.SetClip($facePath)

    # --- sweep wedge ------------------------------------------------------
    $lead  = -60.0          # leading edge, degrees (up and to the right)
    $span  = 95.0
    $steps = 30
    # back to front, so the brighter slice always paints over the dimmer one
    for ($i = $steps - 1; $i -ge 0; $i--) {
        $a0 = $lead - ($span * ($i + 1) / $steps)
        $frac = 1.0 - ($i / [double]$steps)
        $alpha = [int](120 * [Math]::Pow($frac, 1.9))
        if ($alpha -le 0) { continue }
        $b = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb($alpha, 62, 232, 142))
        # generous overlap: abutting antialiased pies leave visible seams
        $g.FillPie($b, ($c-$F), ($c-$F), (2*$F), (2*$F), $a0, ($span / $steps + 3.0))
        $b.Dispose()
    }

    # --- grid -------------------------------------------------------------
    $lw = [Math]::Max(1.0, $s * 0.020)
    if ($mid) {
        $ringPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(170, 58, 214, 133), $lw)
        $r = $F * 0.55
        $g.DrawEllipse($ringPen, ($c-$r), ($c-$r), (2*$r), (2*$r))
    }
    if ($detail) {
        $crossPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(105, 58, 214, 133), $lw)
        $g.DrawLine($crossPen, ($c-$F), $c, ($c+$F), $c)
        $g.DrawLine($crossPen, $c, ($c-$F), $c, ($c+$F))
    }

    # --- leading edge of the sweep ---------------------------------------
    $rad = $lead * [Math]::PI / 180.0
    $edgePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 176, 255, 205), ($lw * 1.4))
    $edgePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $edgePen.EndCap   = [System.Drawing.Drawing2D.LineCap]::Round
    $g.DrawLine($edgePen, $c, $c, ($c + $F * [Math]::Cos($rad)), ($c + $F * [Math]::Sin($rad)))

    # --- the contact ------------------------------------------------------
    $bAng  = -14.0 * [Math]::PI / 180.0
    $bDist = $F * 0.60
    $bx = $c + $bDist * [Math]::Cos($bAng)
    $by = $c + $bDist * [Math]::Sin($bAng)
    $glowR = $F * 0.30
    $glowPath = New-Object System.Drawing.Drawing2D.GraphicsPath
    $glowPath.AddEllipse(($bx-$glowR), ($by-$glowR), (2*$glowR), (2*$glowR))
    $glow = New-Object System.Drawing.Drawing2D.PathGradientBrush($glowPath)
    $glow.CenterColor    = [System.Drawing.Color]::FromArgb(205, 196, 255, 122)
    $glow.SurroundColors = @([System.Drawing.Color]::FromArgb(0, 196, 255, 122))
    $g.FillEllipse($glow, ($bx-$glowR), ($by-$glowR), (2*$glowR), (2*$glowR))
    $blipR = $F * 0.145
    $blip = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 226, 255, 178))
    $g.FillEllipse($blip, ($bx-$blipR), ($by-$blipR), (2*$blipR), (2*$blipR))

    $g.ResetClip()

    # --- bezel ------------------------------------------------------------
    $bezelW = ($R - $F) * 1.35
    $bezelR = $F + $bezelW / 2.0 - $s * 0.004
    $bezel = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 80, 240, 155), $bezelW)
    $g.DrawEllipse($bezel, ($c-$bezelR), ($c-$bezelR), (2*$bezelR), (2*$bezelR))

    $g.Dispose()

    $final = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $fg = [System.Drawing.Graphics]::FromImage($final)
    $fg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $fg.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $fg.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $fg.Clear([System.Drawing.Color]::Transparent)
    $fg.DrawImage($bmp, (New-Object System.Drawing.Rectangle(0, 0, $size, $size)))
    $fg.Dispose()
    $bmp.Dispose()
    return $final
}

$sizes = @(16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
$pngs = @()
foreach ($sz in $sizes) {
    $bm = New-RadarBitmap $sz
    $ms = New-Object System.IO.MemoryStream
    $bm.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngs += ,@($sz, $ms.ToArray())
    $bm.Dispose()
    $ms.Dispose()
}

$fs = New-Object System.IO.FileStream($out, [System.IO.FileMode]::Create)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]$pngs.Count)
$offset = 6 + 16 * $pngs.Count
foreach ($p in $pngs) {
    $sz = $p[0]; $data = $p[1]
    $dim = if ($sz -ge 256) { 0 } else { $sz }
    $bw.Write([byte]$dim); $bw.Write([byte]$dim)
    $bw.Write([byte]0); $bw.Write([byte]0)
    $bw.Write([UInt16]1); $bw.Write([UInt16]32)
    $bw.Write([UInt32]$data.Length); $bw.Write([UInt32]$offset)
    $offset += $data.Length
}
foreach ($p in $pngs) { $bw.Write($p[1]) }
$bw.Flush(); $bw.Close(); $fs.Close()

Write-Output "wrote $out ($((Get-Item $out).Length) bytes)"
