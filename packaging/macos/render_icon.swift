// Deterministic AppKit rendering of Assets/ScribeLoop.svg geometry.
import AppKit

let root = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
func render(_ size:Int) throws -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes:nil,pixelsWide:size,pixelsHigh:size,
        bitsPerSample:8,samplesPerPixel:4,hasAlpha:true,isPlanar:false,
        colorSpaceName:.deviceRGB,bytesPerRow:0,bitsPerPixel:0)!
    let context = NSGraphicsContext(bitmapImageRep:rep)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = context
    let transform = NSAffineTransform()
    transform.translateX(by:0,yBy:CGFloat(size)); transform.scaleX(by:CGFloat(size)/1024,yBy:-CGFloat(size)/1024); transform.concat()
    let tile = NSBezierPath(roundedRect:NSRect(x:64,y:64,width:896,height:896),xRadius:204,yRadius:204)
    NSGradient(starting:NSColor(srgbRed:40/255,green:121/255,blue:108/255,alpha:1),
        ending:NSColor(srgbRed:18/255,green:62/255,blue:55/255,alpha:1))!.draw(in:tile,angle:60)
    NSColor.white.withAlphaComponent(0.16).setStroke(); tile.lineWidth = 2; tile.stroke()
    let ink = NSColor(srgbRed:244/255,green:240/255,blue:223/255,alpha:1)
    ink.setStroke()
    let loop = NSBezierPath(); loop.move(to:NSPoint(x:650,y:296)); loop.line(to:NSPoint(x:422,y:296))
    loop.curve(to:NSPoint(x:276,y:438),controlPoint1:NSPoint(x:336,y:296),controlPoint2:NSPoint(x:276,y:357))
    loop.line(to:NSPoint(x:276,y:558))
    loop.curve(to:NSPoint(x:422,y:704),controlPoint1:NSPoint(x:276,y:641),controlPoint2:NSPoint(x:336,y:704))
    loop.line(to:NSPoint(x:550,y:704)); loop.line(to:NSPoint(x:650,y:780)); loop.line(to:NSPoint(x:650,y:688))
    loop.curve(to:NSPoint(x:756,y:538),controlPoint1:NSPoint(x:718,y:664),controlPoint2:NSPoint(x:756,y:609))
    loop.line(to:NSPoint(x:756,y:438))
    loop.curve(to:NSPoint(x:650,y:296),controlPoint1:NSPoint(x:756,y:357),controlPoint2:NSPoint(x:716,y:296))
    loop.close(); loop.lineWidth = 60; loop.lineJoinStyle = .round; loop.stroke()
    let wave = NSBezierPath(); wave.lineWidth = 44; wave.lineCapStyle = .round
    for (x,a,b) in [(418,461,553),(518,414,600),(618,461,553)] {
        wave.move(to:NSPoint(x:x,y:a)); wave.line(to:NSPoint(x:x,y:b))
    }
    wave.stroke(); NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using:.png,properties:[:])!
}
for size in [16,32,128,256,512] {
    try render(size).write(to:root.appendingPathComponent("icon_\(size)x\(size).png"))
    try render(size*2).write(to:root.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
