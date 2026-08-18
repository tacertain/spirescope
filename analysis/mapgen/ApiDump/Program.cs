// Dump the shape of types in the installed sts2.dll.
//
// The headless tool was written against a different game build, so method names
// drift. This prints members for named types (or a substring search) so the
// mismatch can be found without guessing.
//
//   dotnet run --project tools/ApiDump -- <dll> type <TypeName> [...]
//   dotnet run --project tools/ApiDump -- <dll> find <substring>
using System;
using System.Linq;
using Mono.Cecil;

var dll = args[0];
var mode = args.Length > 1 ? args[1] : "type";
var names = args.Skip(2).ToArray();

var resolver = new DefaultAssemblyResolver();
resolver.AddSearchDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(dll)));
var module = ModuleDefinition.ReadModule(dll, new ReaderParameters
{
    AssemblyResolver = resolver,
    ReadingMode = ReadingMode.Deferred,
});

static string Sig(MethodDefinition m) =>
    $"{(m.IsPublic ? "public " : m.IsPrivate ? "private " : "")}"
    + $"{(m.IsStatic ? "static " : "")}{m.ReturnType.Name} {m.Name}("
    + string.Join(", ", m.Parameters.Select(p => $"{p.ParameterType.Name} {p.Name}")) + ")";

if (mode == "find")
{
    var needle = names[0];
    foreach (var t in module.Types)
    {
        foreach (var m in t.Methods)
        {
            if (m.Name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                Console.WriteLine($"{t.FullName}::{Sig(m)}");
        }
    }
    return;
}

foreach (var name in names)
{
    var t = module.Types.FirstOrDefault(x => x.Name == name)
            ?? module.Types.FirstOrDefault(x => x.FullName == name);
    if (t == null)
    {
        Console.WriteLine($"== {name}: NOT FOUND ==");
        continue;
    }
    Console.WriteLine($"== {t.FullName} ==");
    foreach (var f in t.Fields.Where(f => f.IsPublic))
        Console.WriteLine($"   field  {f.FieldType.Name} {f.Name}");
    foreach (var p in t.Properties)
        Console.WriteLine($"   prop   {p.PropertyType.Name} {p.Name}");
    foreach (var m in t.Methods.Where(m => !m.IsGetter && !m.IsSetter))
        Console.WriteLine($"   method {Sig(m)}");
    Console.WriteLine();
}
