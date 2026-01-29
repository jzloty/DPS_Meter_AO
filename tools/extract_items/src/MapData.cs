using System.Text;
using System.Text.Json;
using System.Xml;

namespace StatisticAnalysisTool.Extractor;

internal static class MapData
{
    public static async Task CreateMapIndexAsync(
        string mainGameFolder,
        LocalizationData localizationData,
        string outputFolderPath,
        string outputFileNameWithExtension = "map_index.json")
    {
        var worldBinPath = Path.Combine(
            mainGameFolder,
            ".\\Albion-Online_Data\\StreamingAssets\\GameData\\cluster\\world.bin"
        );

        var worldDataByteArray = await BinaryDecrypter.DecryptAndDecompressAsync(worldBinPath);
        var xmlDoc = new XmlDocument();
        xmlDoc.LoadXml(RemoveNonPrintableCharacters(Encoding.UTF8.GetString(RemoveBom(worldDataByteArray.ToArray()))));

        var mapping = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var nodes = xmlDoc.SelectNodes("//*[@id and @displayname]");
        if (nodes != null)
        {
            foreach (XmlNode node in nodes)
            {
                var id = node.Attributes?["id"]?.Value;
                var display = node.Attributes?["displayname"]?.Value;
                if (string.IsNullOrWhiteSpace(id) || string.IsNullOrWhiteSpace(display))
                {
                    continue;
                }

                var name = ResolveLocalization(localizationData, display) ?? display;
                if (!mapping.ContainsKey(id))
                {
                    mapping[id] = name;
                }
            }
        }

        Directory.CreateDirectory(outputFolderPath);
        var outputPath = Path.Combine(outputFolderPath, outputFileNameWithExtension);
        var jsonOptions = new JsonSerializerOptions
        {
            WriteIndented = true,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
        };
        var json = JsonSerializer.Serialize(mapping, jsonOptions);
        await File.WriteAllTextAsync(outputPath, json);
    }

    private static string? ResolveLocalization(LocalizationData data, string key)
    {
        if (data.AllLocalized.TryGetValue(key, out var translations))
        {
            if (translations.TryGetValue("EN-US", out var en))
            {
                return en;
            }
            if (translations.Count > 0)
            {
                return translations.First().Value;
            }
        }
        return null;
    }

    private static string RemoveNonPrintableCharacters(string input)
    {
        return new string(input.Where(c => !char.IsControl(c) || char.IsWhiteSpace(c)).ToArray());
    }

    private static byte[] RemoveBom(byte[] byteArray)
    {
        byte[] utf8Bom = [0xEF, 0xBB, 0xBF];

        if (byteArray.Length >= utf8Bom.Length && byteArray[0] == utf8Bom[0] && byteArray[1] == utf8Bom[1] && byteArray[2] == utf8Bom[2])
        {
            return byteArray.Skip(utf8Bom.Length).ToArray();
        }

        return byteArray;
    }
}
