using StatisticAnalysisTool.Extractor;
using StatisticAnalysisTool.Extractor.Enums;

static int Usage()
{
    Console.WriteLine("Usage:");
    Console.WriteLine("  ExtractItems --game-root <path> [--output <path>] [--server <live|staging|playground>]");
    Console.WriteLine();
    Console.WriteLine("Example:");
    Console.WriteLine(@"  ExtractItems --game-root ""C:\Program Files\Albion Online"" --output ""data""");
    return 1;
}

static string? NextArg(string[] args, ref int index)
{
    if (index + 1 >= args.Length)
    {
        return null;
    }
    index++;
    return args[index];
}

static ServerType ParseServer(string? value)
{
    return value?.ToLowerInvariant() switch
    {
        "staging" => ServerType.Staging,
        "playground" => ServerType.Playground,
        _ => ServerType.Live
    };
}

string? gameRoot = null;
string outputDir = "data";
ServerType serverType = ServerType.Live;

for (var i = 0; i < args.Length; i++)
{
    var arg = args[i];
    if (arg is "--help" or "-h")
    {
        return Usage();
    }
    if (arg is "--game-root" or "-g")
    {
        gameRoot = NextArg(args, ref i);
        continue;
    }
    if (arg is "--output" or "-o")
    {
        var val = NextArg(args, ref i);
        if (!string.IsNullOrWhiteSpace(val))
        {
            outputDir = val;
        }
        continue;
    }
    if (arg is "--server" or "-s")
    {
        serverType = ParseServer(NextArg(args, ref i));
        continue;
    }
}

if (string.IsNullOrWhiteSpace(gameRoot))
{
    return Usage();
}

Directory.CreateDirectory(outputDir);

if (!Extractor.IsValidMainGameFolder(gameRoot, serverType))
{
    Console.WriteLine("Invalid game root. Expected structure:");
    Console.WriteLine(@"  <game-root>\game\Albion-Online_Data\StreamingAssets\GameData\items.bin");
    Console.WriteLine(@"  <game-root>\game\Albion-Online_Data\StreamingAssets\GameData\localization.bin");
    return 2;
}

var extractor = new Extractor(gameRoot, serverType);
try
{
    await extractor.ExtractIndexedItemGameDataAsync(outputDir, "indexedItems.json");
    await extractor.ExtractGameDataAsync(outputDir, new[] { "items" });
    await extractor.ExtractMapIndexAsync(outputDir, "map_index.json");
    Console.WriteLine($"Generated: {Path.Combine(outputDir, "indexedItems.json")}");
    Console.WriteLine($"Generated: {Path.Combine(outputDir, "items.json")}");
    Console.WriteLine($"Generated: {Path.Combine(outputDir, "map_index.json")}");
    return 0;
}
finally
{
    extractor.Dispose();
}
