<?php
/**
 * NasManager 更新目录列表
 * 放到 /volume1/web/download/NasManager/index.php
 * 访问 https://duadu.cc/download/NasManager/ 时自动列出所有安装包
 * 软件端正则 NasManager_Setup_X.Y.Z.exe 可直接匹配
 */
header('Content-Type: text/html; charset=utf-8');

$files = glob('NasManager_Setup_*.exe');
if (!$files) $files = array();

// 按版本号排序
usort($files, function($a, $b) {
    preg_match('/(\d+\.\d+\.\d+)/', $a, $m1);
    preg_match('/(\d+\.\d+\.\d+)/', $b, $m2);
    return version_compare($m1[1], $m2[1]);
});

echo "<!DOCTYPE html><html><head><meta charset='utf-8'><title>NasManager Downloads</title></head>";
echo "<body><h1>Index of /download/NasManager/</h1><pre><hr />";
echo "<a href='../'>../</a>\n";
foreach ($files as $f) {
    $size = round(filesize($f) / 1048576, 2);
    $date = date('Y-m-d H:i', filemtime($f));
    $name = htmlspecialchars($f);
    echo "<a href=\"$name\">$name</a>" . str_repeat(' ', max(2, 55 - strlen($f))) . "$date  {$size}M\n";
}
echo "<hr /></pre></body></html>";
