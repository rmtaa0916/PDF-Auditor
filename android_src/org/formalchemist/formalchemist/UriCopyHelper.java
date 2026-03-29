package org.formalchemist.formalchemist;

import android.content.ContentResolver;
import android.content.Context;
import android.content.res.AssetFileDescriptor;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;

public class UriCopyHelper {
    public static String resolveDisplayName(Context context, String uriString, String defaultName) {
        if (context == null || uriString == null || uriString.trim().isEmpty()) {
            return defaultName;
        }

        Uri uri = Uri.parse(uriString);
        ContentResolver resolver = context.getContentResolver();
        Cursor cursor = null;

        try {
            cursor = resolver.query(uri, null, null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (idx >= 0) {
                    String name = cursor.getString(idx);
                    if (name != null && !name.trim().isEmpty()) {
                        return name;
                    }
                }
            }
        } catch (Exception ignored) {
        } finally {
            try {
                if (cursor != null) cursor.close();
            } catch (Exception ignored) {}
        }

        try {
            String seg = uri.getLastPathSegment();
            if (seg != null && !seg.trim().isEmpty()) {
                return seg;
            }
        } catch (Exception ignored) {
        }

        return defaultName;
    }

    public static boolean copyUriToPath(Context context, String uriString, String targetPath) {
        if (context == null || uriString == null || uriString.trim().isEmpty() || targetPath == null || targetPath.trim().isEmpty()) {
            return false;
        }

        Uri uri = Uri.parse(uriString);
        ContentResolver resolver = context.getContentResolver();
        InputStream in = null;
        AssetFileDescriptor afd = null;
        FileOutputStream out = null;

        try {
            File target = new File(targetPath);
            File parent = target.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }

            try {
                afd = resolver.openAssetFileDescriptor(uri, "r");
                if (afd != null) {
                    in = afd.createInputStream();
                }
            } catch (Exception ignored) {
                in = null;
            }

            if (in == null) {
                in = resolver.openInputStream(uri);
            }
            if (in == null) {
                return false;
            }

            out = new FileOutputStream(target, false);
            byte[] buffer = new byte[65536];
            int count;
            while ((count = in.read(buffer)) != -1) {
                if (count > 0) {
                    out.write(buffer, 0, count);
                }
            }
            out.flush();
            try {
                out.getFD().sync();
            } catch (Exception ignored) {
            }

            return target.exists() && target.length() > 0;
        } catch (Exception e) {
            try {
                File target = new File(targetPath);
                if (target.exists()) {
                    target.delete();
                }
            } catch (Exception ignored) {
            }
            return false;
        } finally {
            try {
                if (out != null) out.close();
            } catch (Exception ignored) {}
            try {
                if (in != null) in.close();
            } catch (Exception ignored) {}
            try {
                if (afd != null) afd.close();
            } catch (Exception ignored) {}
        }
    }
}
