# 発表者アイコンを公式サイトから取ってくる（リポジトリには含めない）
$base = "https://storage.googleapis.com/studio-design-asset-files/projects/EjOQwn7oaJ/"
$map = @{
 "ykpythemind"      = "s-512x512_webp_e6210b92-d4fd-4245-86a8-8d69e84d6218.webp"
 "shia"             = "s-512x512_webp_7db6e3c2-fb91-46e2-a7d7-68b1c0991e7a.webp"
 "kitapashi"        = "s-460x460_webp_a83d631c-7622-4827-b356-460ed01528f3.webp"
 "wattanx"          = "s-460x460_webp_5dd5c822-0cd4-4ede-a2ba-a1e2d96218b6.webp"
 "mario"            = "s-512x512_webp_dafef18f-cc49-4681-a995-d666056bbd86.webp"
 "sugiyama"         = "s-3984x2656_v-frms_webp_b06d7f85-343c-46c9-bf9f-156dfa87dd8a_small.webp"
 "satomi-nishiyama" = "s-1024x1024_v-fs_webp_545b970f-432b-47cb-a04a-b7e801ac97b2_small.webp"
}
$dir = Join-Path $PSScriptRoot "..\assets\speakers"
New-Item -ItemType Directory -Force $dir | Out-Null
foreach ($k in $map.Keys) {
    Invoke-WebRequest -Uri ($base + $map[$k]) -OutFile (Join-Path $dir "$k.webp") -UseBasicParsing
}
"{0} 枚を {1} に保存" -f $map.Count, $dir
