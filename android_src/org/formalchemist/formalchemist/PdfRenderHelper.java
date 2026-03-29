package org.formalchemist.formalchemist;

import android.graphics.Bitmap;
import android.graphics.pdf.PdfRenderer;
import android.os.ParcelFileDescriptor;

import java.io.File;
import java.io.FileOutputStream;

public class PdfRenderHelper {
    public static String renderPageToPng(String pdfPath, int pageIndex, float zoom, String outPath) throws Exception {
        ParcelFileDescriptor pfd = null;
        PdfRenderer renderer = null;
        PdfRenderer.Page page = null;
        FileOutputStream fos = null;
        try {
            File pdfFile = new File(pdfPath);
            pfd = ParcelFileDescriptor.open(pdfFile, ParcelFileDescriptor.MODE_READ_ONLY);
            renderer = new PdfRenderer(pfd);
            int total = renderer.getPageCount();
            if (total <= 0) {
                throw new IllegalArgumentException("PDF has no pages");
            }
            if (pageIndex < 0) pageIndex = 0;
            if (pageIndex >= total) pageIndex = total - 1;
            page = renderer.openPage(pageIndex);

            int width = Math.max(1, (int) (page.getWidth() * zoom));
            int height = Math.max(1, (int) (page.getHeight() * zoom));
            Bitmap bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
            bitmap.eraseColor(0xFFFFFFFF);
            page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);

            File outFile = new File(outPath);
            fos = new FileOutputStream(outFile);
            boolean ok = bitmap.compress(Bitmap.CompressFormat.PNG, 100, fos);
            fos.flush();
            if (!ok) {
                throw new IllegalStateException("Bitmap compression failed");
            }
            return outFile.getAbsolutePath();
        } finally {
            if (fos != null) try { fos.close(); } catch (Exception ignored) {}
            if (page != null) try { page.close(); } catch (Exception ignored) {}
            if (renderer != null) try { renderer.close(); } catch (Exception ignored) {}
            if (pfd != null) try { pfd.close(); } catch (Exception ignored) {}
        }
    }
}
