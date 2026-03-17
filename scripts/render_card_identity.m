#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreImage/CoreImage.h>
#import <Foundation/Foundation.h>

static void Die(NSString *message) {
    fprintf(stderr, "%s\n", message.UTF8String);
    exit(1);
}

static NSDictionary *LoadManifest(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        Die([NSString stringWithFormat:@"Unable to read manifest at %@", path]);
    }

    NSError *error = nil;
    NSDictionary *manifest = [NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
    if (manifest == nil) {
        Die([NSString stringWithFormat:@"Unable to decode manifest: %@", error.localizedDescription]);
    }

    return manifest;
}

static CGImageRef CGImageFromNSImage(NSImage *image) {
    CGRect rect = CGRectMake(0, 0, image.size.width, image.size.height);
    CGImageRef cgImage = [image CGImageForProposedRect:&rect context:nil hints:nil];
    if (cgImage == nil) {
        Die(@"Unable to create CGImage from NSImage");
    }
    return CGImageRetain(cgImage);
}

static NSRect AppKitRectFromTopLeft(NSDictionary *rectDict, NSInteger cardHeight) {
    CGFloat x = [rectDict[@"x"] doubleValue];
    CGFloat y = [rectDict[@"y"] doubleValue];
    CGFloat width = [rectDict[@"width"] doubleValue];
    CGFloat height = [rectDict[@"height"] doubleValue];
    return NSMakeRect(x, (CGFloat)cardHeight - y - height, width, height);
}

static NSRect CanvasRectFromTopLeft(CGFloat x, CGFloat y, CGFloat width, CGFloat height, NSInteger canvasHeight) {
    return NSMakeRect(x, (CGFloat)canvasHeight - y - height, width, height);
}

static NSImage *CropPortrait(NSString *sourcePath, CGRect topLeftCrop) {
    NSImage *sourceImage = [[NSImage alloc] initWithContentsOfFile:sourcePath];
    if (sourceImage == nil) {
        Die([NSString stringWithFormat:@"Unable to load portrait source %@", sourcePath]);
    }

    CGImageRef sourceCG = CGImageFromNSImage(sourceImage);
    CGRect convertedRect = CGRectMake(
        topLeftCrop.origin.x,
        (CGFloat)CGImageGetHeight(sourceCG) - topLeftCrop.origin.y - topLeftCrop.size.height,
        topLeftCrop.size.width,
        topLeftCrop.size.height
    );
    CGImageRef cropped = CGImageCreateWithImageInRect(sourceCG, convertedRect);
    CGImageRelease(sourceCG);
    if (cropped == nil) {
        Die([NSString stringWithFormat:@"Unable to crop portrait %@", sourcePath]);
    }

    NSImage *result = [[NSImage alloc] initWithCGImage:cropped size:NSMakeSize(topLeftCrop.size.width, topLeftCrop.size.height)];
    CGImageRelease(cropped);
    return result;
}

static NSDictionary *MakePortraitLibrary(NSURL *rootURL) {
    NSString *majorPath = [rootURL URLByAppendingPathComponent:@"Fake_identity/cni majeur.png"].path;
    NSString *minorPath = [rootURL URLByAppendingPathComponent:@"Fake_identity/cni mineur.jpg"].path;

    NSImage *major = CropPortrait(majorPath, CGRectMake(120, 190, 430, 570));
    NSImage *minor = CropPortrait(minorPath, CGRectMake(105, 140, 340, 500));

    return @{
        @"major": major,
        @"minor": minor,
    };
}

static void DrawPatternLayer(NSSize size) {
    [[NSColor colorWithCalibratedRed:0.79 green:0.85 blue:0.92 alpha:0.32] setStroke];
    for (NSInteger index = 0; index < 26; index++) {
        NSBezierPath *path = [NSBezierPath bezierPath];
        CGFloat baseY = 40.0 + (CGFloat)index * 30.0;
        [path moveToPoint:NSMakePoint(-20.0, baseY)];
        for (NSInteger step = 0; step <= 16; step++) {
            CGFloat x = (CGFloat)step * size.width / 16.0;
            CGFloat y = baseY + sin(((CGFloat)step + (CGFloat)index) * 0.65) * 16.0;
            [path lineToPoint:NSMakePoint(x, y)];
        }
        [path setLineWidth:1.5];
        [path stroke];
    }
}

static NSAttributedString *AttributedString(NSString *text, NSFont *font, NSColor *color, NSTextAlignment alignment) {
    NSMutableParagraphStyle *paragraph = [[NSMutableParagraphStyle alloc] init];
    paragraph.alignment = alignment;

    NSDictionary *attributes = @{
        NSFontAttributeName: font,
        NSForegroundColorAttributeName: color,
        NSParagraphStyleAttributeName: paragraph,
    };

    return [[NSAttributedString alloc] initWithString:text attributes:attributes];
}

static void DrawPortraitVariant(NSString *variant, NSDictionary *portraits, NSRect rect) {
    NSString *baseKey = [variant hasPrefix:@"minor"] ? @"minor" : @"major";
    NSImage *source = portraits[baseKey];
    if (source == nil) {
        return;
    }

    [NSGraphicsContext saveGraphicsState];
    NSBezierPath *clipPath = [NSBezierPath bezierPathWithRoundedRect:rect xRadius:16 yRadius:16];
    [clipPath addClip];

    if ([variant containsString:@"mirror"]) {
        NSAffineTransform *transform = [NSAffineTransform transform];
        [transform translateXBy:NSMaxX(rect) yBy:NSMinY(rect)];
        [transform scaleXBy:-1.0 yBy:1.0];
        [transform concat];
        [source drawInRect:NSMakeRect(0, 0, rect.size.width, rect.size.height)
                  fromRect:NSZeroRect
                 operation:NSCompositingOperationSourceOver
                  fraction:1.0];
    } else {
        [source drawInRect:rect
                  fromRect:NSZeroRect
                 operation:NSCompositingOperationSourceOver
                  fraction:1.0];
    }

    if ([variant containsString:@"cool"]) {
        [[NSColor colorWithCalibratedRed:0.82 green:0.91 blue:1.0 alpha:0.14] setFill];
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceOver);
    } else if ([variant containsString:@"warm"]) {
        [[NSColor colorWithCalibratedRed:1.0 green:0.92 blue:0.84 alpha:0.14] setFill];
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceOver);
    } else if ([variant containsString:@"gray"]) {
        [[NSColor colorWithCalibratedWhite:0.96 alpha:0.18] setFill];
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceOver);
    }

    [NSGraphicsContext restoreGraphicsState];

    [[NSColor colorWithCalibratedWhite:1.0 alpha:0.78] setStroke];
    NSBezierPath *border = [NSBezierPath bezierPathWithRoundedRect:rect xRadius:16 yRadius:16];
    [border setLineWidth:3.0];
    [border stroke];
}

static NSImage *RenderCard(NSDictionary *profile, NSDictionary *layout, NSInteger cardWidth, NSInteger cardHeight, NSDictionary *portraits) {
    NSImage *image = [[NSImage alloc] initWithSize:NSMakeSize(cardWidth, cardHeight)];
    [image lockFocus];

    NSRect fullRect = NSMakeRect(0, 0, cardWidth, cardHeight);
    [[NSColor colorWithCalibratedRed:0.93 green:0.96 blue:0.99 alpha:1.0] setFill];
    NSRectFill(fullRect);

    NSGradient *gradient = [[NSGradient alloc] initWithColors:@[
        [NSColor colorWithCalibratedRed:0.84 green:0.91 blue:0.99 alpha:1.0],
        [NSColor colorWithCalibratedRed:0.96 green:0.97 blue:0.94 alpha:1.0],
    ]];
    [gradient drawInBezierPath:[NSBezierPath bezierPathWithRoundedRect:fullRect xRadius:34 yRadius:34] angle:12.0];

    DrawPatternLayer(NSMakeSize(cardWidth, cardHeight));

    [[NSColor colorWithCalibratedRed:0.79 green:0.86 blue:0.97 alpha:0.18] setFill];
    NSRectFillUsingOperation(NSMakeRect(580, 160, 760, 540), NSCompositingOperationSourceOver);

    NSFont *titleFont = [NSFont systemFontOfSize:56 weight:NSFontWeightBold];
    NSFont *labelFont = [NSFont systemFontOfSize:28 weight:NSFontWeightRegular];
    NSFont *valueFont = [NSFont systemFontOfSize:54 weight:NSFontWeightBold];
    NSFont *lineValueFont = [NSFont systemFontOfSize:40 weight:NSFontWeightBold];
    NSFont *smallFont = [NSFont monospacedDigitSystemFontOfSize:42 weight:NSFontWeightSemibold];
    NSFont *signatureFont = [NSFont fontWithName:@"Times-Italic" size:54];
    if (signatureFont == nil) {
        signatureFont = [NSFont systemFontOfSize:54 weight:NSFontWeightRegular];
    }

    NSColor *titleColor = [NSColor colorWithCalibratedRed:0.14 green:0.20 blue:0.36 alpha:1.0];
    NSColor *secondaryTitleColor = [NSColor colorWithCalibratedRed:0.19 green:0.25 blue:0.42 alpha:1.0];
    NSColor *labelColor = [NSColor colorWithCalibratedRed:0.26 green:0.38 blue:0.70 alpha:1.0];
    NSColor *valueColor = [NSColor colorWithCalibratedRed:0.07 green:0.12 blue:0.22 alpha:1.0];

    [AttributedString(@"REPUBLIQUE FRANCAISE", titleFont, titleColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(78, 58, 780, 64, cardHeight)];
    [AttributedString(@"CARTE D'IDENTITE", [NSFont systemFontOfSize:40 weight:NSFontWeightBold], secondaryTitleColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(900, 72, 420, 50, cardHeight)];

    NSRect photoRect = CanvasRectFromTopLeft(85, 175, 360, 485, cardHeight);
    DrawPortraitVariant(profile[@"portrait_variant"], portraits, photoRect);

    [AttributedString(@"Nom :", labelFont, labelColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(560, 132, 180, 40, cardHeight)];
    [AttributedString(profile[@"nom"], valueFont, valueColor, NSTextAlignmentLeft)
        drawInRect:AppKitRectFromTopLeft(layout[@"field_rects"][@"nom"], cardHeight)];

    [AttributedString(@"Prenom :", labelFont, labelColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(560, 292, 220, 40, cardHeight)];
    [AttributedString(profile[@"prenom"], valueFont, valueColor, NSTextAlignmentLeft)
        drawInRect:AppKitRectFromTopLeft(layout[@"field_rects"][@"prenom"], cardHeight)];

    [AttributedString(@"Nationalite :", labelFont, labelColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(500, 482, 220, 36, cardHeight)];
    [AttributedString(profile[@"nationalite"], lineValueFont, valueColor, NSTextAlignmentLeft)
        drawInRect:AppKitRectFromTopLeft(layout[@"field_rects"][@"nationalite"], cardHeight)];

    [AttributedString(@"Date de naissance :", labelFont, labelColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(500, 574, 310, 38, cardHeight)];
    [AttributedString(profile[@"date_naissance"], lineValueFont, valueColor, NSTextAlignmentLeft)
        drawInRect:AppKitRectFromTopLeft(layout[@"field_rects"][@"date_naissance"], cardHeight)];

    [AttributedString(@"Sexe :", labelFont, labelColor, NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(500, 686, 120, 36, cardHeight)];
    [AttributedString(profile[@"sexe"], lineValueFont, valueColor, NSTextAlignmentLeft)
        drawInRect:AppKitRectFromTopLeft(layout[@"field_rects"][@"sexe"], cardHeight)];

    [AttributedString(profile[@"card_number"], smallFont, [NSColor colorWithCalibratedRed:0.19 green:0.27 blue:0.56 alpha:1.0], NSTextAlignmentLeft)
        drawInRect:CanvasRectFromTopLeft(86, 760, 460, 50, cardHeight)];
    [AttributedString(profile[@"signature"], signatureFont, [NSColor colorWithCalibratedWhite:0.18 alpha:0.96], NSTextAlignmentCenter)
        drawInRect:CanvasRectFromTopLeft(860, 730, 430, 90, cardHeight)];

    [[NSColor colorWithCalibratedWhite:1.0 alpha:0.66] setStroke];
    NSBezierPath *cardBorder = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(2, 2, cardWidth - 4, cardHeight - 4) xRadius:34 yRadius:34];
    [cardBorder setLineWidth:4.0];
    [cardBorder stroke];

    [image unlockFocus];
    return image;
}

static CGPoint TopLeftToBottomLeft(NSArray *point, NSInteger canvasHeight) {
    CGFloat x = [point[0] doubleValue];
    CGFloat y = [point[1] doubleValue];
    return CGPointMake(x, (CGFloat)canvasHeight - y);
}

static void RenderEntry(NSDictionary *entry, NSDictionary *manifest, NSDictionary *portraits) {
    NSInteger cardWidth = [manifest[@"card_width"] integerValue];
    NSInteger cardHeight = [manifest[@"card_height"] integerValue];
    NSInteger canvasWidth = [entry[@"canvas_width"] integerValue];
    NSInteger canvasHeight = [entry[@"canvas_height"] integerValue];

    NSArray *quad = entry[@"document_quad"];
    CGPoint topLeft = TopLeftToBottomLeft(quad[0], canvasHeight);
    CGPoint topRight = TopLeftToBottomLeft(quad[1], canvasHeight);
    CGPoint bottomRight = TopLeftToBottomLeft(quad[2], canvasHeight);
    CGPoint bottomLeft = TopLeftToBottomLeft(quad[3], canvasHeight);

    NSImage *flatCard = RenderCard(entry[@"profile"], manifest[@"layout"], cardWidth, cardHeight, portraits);

    NSImage *canvasImage = [[NSImage alloc] initWithSize:NSMakeSize(canvasWidth, canvasHeight)];
    [canvasImage lockFocus];

    [[NSColor colorWithCalibratedRed:0.97 green:0.97 blue:0.96 alpha:1.0] setFill];
    NSRectFill(NSMakeRect(0, 0, canvasWidth, canvasHeight));

    NSAffineTransformStruct transformStruct;
    transformStruct.m11 = (bottomRight.x - bottomLeft.x) / (CGFloat)cardWidth;
    transformStruct.m12 = (bottomRight.y - bottomLeft.y) / (CGFloat)cardWidth;
    transformStruct.m21 = (topLeft.x - bottomLeft.x) / (CGFloat)cardHeight;
    transformStruct.m22 = (topLeft.y - bottomLeft.y) / (CGFloat)cardHeight;
    transformStruct.tX = bottomLeft.x;
    transformStruct.tY = bottomLeft.y;

    [NSGraphicsContext saveGraphicsState];
    NSAffineTransform *transform = [NSAffineTransform transform];
    [transform setTransformStruct:transformStruct];
    [transform concat];
    [flatCard drawInRect:NSMakeRect(0, 0, cardWidth, cardHeight)
                fromRect:NSZeroRect
               operation:NSCompositingOperationSourceOver
                fraction:1.0];
    [NSGraphicsContext restoreGraphicsState];

    [[NSColor colorWithCalibratedWhite:0.85 alpha:0.35] setStroke];
    NSBezierPath *shadowEdge = [NSBezierPath bezierPath];
    [shadowEdge moveToPoint:topLeft];
    [shadowEdge lineToPoint:topRight];
    [shadowEdge lineToPoint:bottomRight];
    [shadowEdge lineToPoint:bottomLeft];
    [shadowEdge closePath];
    [shadowEdge setLineWidth:2.0];
    [shadowEdge stroke];

    [canvasImage unlockFocus];

    CGImageRef finalCG = CGImageFromNSImage(canvasImage);

    NSURL *destination = [NSURL fileURLWithPath:entry[@"image_path"]];
    [[NSFileManager defaultManager] createDirectoryAtURL:[destination URLByDeletingLastPathComponent]
                             withIntermediateDirectories:YES
                                              attributes:nil
                                                   error:nil];

    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithCGImage:finalCG];
    CGImageRelease(finalCG);
    NSData *data = [bitmap representationUsingType:NSBitmapImageFileTypeTIFF properties:@{}];
    if (data == nil || ![data writeToURL:destination atomically:YES]) {
        Die([NSString stringWithFormat:@"Unable to write TIFF %@", destination.path]);
    }
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            Die(@"Usage: render_card_identity.m <manifest-path>");
        }

        NSString *manifestPath = [NSString stringWithUTF8String:argv[1]];
        NSDictionary *manifest = LoadManifest(manifestPath);
        NSURL *rootURL = [NSURL fileURLWithPath:[[NSFileManager defaultManager] currentDirectoryPath]];
        NSDictionary *portraits = MakePortraitLibrary(rootURL);

        for (NSDictionary *entry in manifest[@"entries"]) {
            RenderEntry(entry, manifest, portraits);
        }
    }
    return 0;
}
