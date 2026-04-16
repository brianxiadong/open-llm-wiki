[Setup]
AppName=Open LLM Wiki Confidential Client
AppVersion=0.2.0
DefaultDirName={autopf}\OpenLLMWikiClient
DefaultGroupName=Open LLM Wiki Client
OutputBaseFilename=open-llm-wiki-client-setup
Compression=lzma
SolidCompression=yes

[Files]
Source: "..\..\dist\confidential-client-binary\open-llm-wiki-client\*"; DestDir: "{app}"; Flags: recursesubdirs

[Icons]
Name: "{group}\Open LLM Wiki Client"; Filename: "{app}\open-llm-wiki-client.exe"
Name: "{group}\Uninstall Open LLM Wiki Client"; Filename: "{uninstallexe}"
