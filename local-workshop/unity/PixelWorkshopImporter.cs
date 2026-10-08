// Unity 2022.3 Editor utility. Place this file in Assets/Editor.
// Imports only the explicitly selected atlas.png using its adjacent atlas.json.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class PixelWorkshopImporter
{
    [Serializable] private class Frame { public string name; public int x, y, width, height; }
    [Serializable] private class Atlas { public int ppu; public Frame[] frames; }

    [MenuItem("Assets/像素工坊/按相邻 JSON 切片", true)]
    private static bool CanImport() => Selection.activeObject is Texture2D;

    [MenuItem("Assets/像素工坊/按相邻 JSON 切片")]
    private static void Import()
    {
        var texture = Selection.activeObject as Texture2D;
        if (texture == null) return;
        var path = AssetDatabase.GetAssetPath(texture);
        var jsonPath = Path.ChangeExtension(path, ".json");
        if (!File.Exists(jsonPath)) throw new InvalidOperationException("找不到相邻的 atlas.json，请把 PNG 与 JSON 一起放入 Assets。");
        var data = JsonUtility.FromJson<Atlas>(File.ReadAllText(jsonPath));
        if (data == null || data.ppu <= 0 || data.frames == null || data.frames.Length == 0)
            throw new InvalidOperationException("无效的像素工坊 atlas.json。");
        var importer = AssetImporter.GetAtPath(path) as TextureImporter;
        if (importer == null) return;
        importer.GetSourceTextureWidthAndHeight(out var width, out var height);
        if (data.frames.Any(f => f == null || f.width <= 0 || f.height <= 0 || f.x < 0 || f.y < 0 || f.x + f.width > width || f.y + f.height > height))
            throw new InvalidOperationException("切片超出原始图集范围。");
        importer.textureType = TextureImporterType.Sprite;
        importer.spriteImportMode = SpriteImportMode.Multiple;
        importer.spritePixelsPerUnit = data.ppu;
        importer.filterMode = FilterMode.Point;
        importer.mipmapEnabled = false;
        importer.textureCompression = TextureImporterCompression.Uncompressed;
        importer.npotScale = TextureImporterNPOTScale.None;
        importer.maxTextureSize = Mathf.NextPowerOfTwo(Mathf.Max(width, height));
        importer.alphaIsTransparency = true;
        // Legacy SpriteMetaData remains available in the targeted Unity 2022.3 editor.
#pragma warning disable 0618
        importer.spritesheet = data.frames.Select(f => new SpriteMetaData
        {
            name = f.name,
            rect = new Rect(f.x, height - f.y - f.height, f.width, f.height),
            alignment = (int)SpriteAlignment.Custom,
            pivot = new Vector2(0.5f, 0f)
        }).ToArray();
#pragma warning restore 0618
        importer.SaveAndReimport();
        Debug.Log($"像素工坊：已切出 {data.frames.Length} 帧，PPU={data.ppu}。请按 atlas.json 的 fps 创建动画。");
    }
}
