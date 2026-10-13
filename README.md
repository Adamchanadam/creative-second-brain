# 創造型第二大腦｜Creative Second Brain

把文章、文字和圖片整理成自己的知識庫，再用已保存的材料思考、研究與創作。筆記保留在你指定的本機 Obsidian 資料夾；AI 負責安裝和技術操作。

## 開始使用

到 [最新正式版本](https://github.com/Adamchanadam/creative-second-brain/releases/latest) 下載版本 ZIP，交給能操作你本機檔案的 AI，並使用隨包的 [安裝提示](install-prompt.txt)。AI 會先核對環境、資料位置及所需授權，再按 [安裝指引](references/first-use.md) 處理。

已有知識庫也走同一入口，告訴 AI 要保留原有資料並升級；不要重新建立筆記庫。已提供 `brain-update` 的版本可直接用該入口要求更新。1.9.15 使用者的首次接入方法由上述安裝指引說明。

## 使用範圍

日常功能與入口以 [命令表](spec/command-registry.json) 為準。圖片生成、瀏覽器操作及宿主是否顯示 slash command，取決於實際 AI 工具；安裝成功不代表所有外部能力已驗證。

這個 repo 只包含通用產品原始碼與測試，不包含使用者的筆記、私人工作區或背景排程。正式安裝及更新請使用 Releases 的版本 ZIP，不把 GitHub 自動產生的 Source code ZIP 當作已建置安裝包。
