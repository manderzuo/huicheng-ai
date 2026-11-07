/* AI 执行工作台：快捷入口 · 一键执行 · 对话式脚本写作 */
(()=>{
'use strict';
const panel=document.getElementById('right-panel');
const chat=document.getElementById('rp-chat');
if(!panel||!chat)return;

document.body.append(chat);
chat.classList.add('exec-expanded');
chat.setAttribute('role','dialog');
chat.setAttribute('aria-label','AI 执行工作台');
chat.innerHTML=`<div class="exec-shell">
<header class="exec-head">
  <div class="exec-brand"><span class="exec-brand-mark">AI</span><div><strong>AI 执行工作台</strong><small id="exec-subtitle">选手机 · 选素材 · 一键执行</small></div></div>
  <div class="exec-head-actions"><button id="lead-data-open" type="button">截流数据</button><button id="chat-config" type="button">模型设置</button><button id="exec-expand" type="button">返回群控</button></div>
</header>
<nav class="exec-modes" id="exec-modes">
  <button type="button" data-mode="quick">快捷入口</button>
  <button type="button" data-mode="control">手机控制</button>
  <button type="button" data-mode="write">脚本写作</button>
  <button type="button" data-mode="video">视频生成</button>
</nav>
<div class="exec-layout">
  <aside class="exec-side" id="exec-side"></aside>
  <main class="exec-main">
    <section class="exec-picker">
      <div class="exec-picker-head">
        <strong>选择手机</strong>
        <button id="exec-select-all" type="button">全选在线</button>
        <button id="exec-clear-selection" type="button">清空</button>
        <span id="exec-selection-count">未选择</span>
      </div>
      <div id="exec-phone-grid" class="exec-phone-grid"></div>
    </section>

    <section class="exec-pane" data-pane="quick">
      <div class="exec-asset-block">
        <div class="exec-asset-actions">
          <button id="exec-upload" type="button">导入素材</button>
          <input id="exec-file" type="file" accept="video/mp4,video/quicktime,video/webm,.mkv,image/jpeg,image/png,image/webp" hidden>
          <select id="exec-copy-platform" title="AI 生成文案的风格（抖音 / 小红书）">
            <option value="douyin">抖音风格</option>
            <option value="xiaohongshu">小红书风格</option>
          </select>
          <button id="exec-copy" type="button">AI 生成文案</button>
          <span id="exec-copy-status" class="exec-copy-status"></span>
        </div>
        <div class="exec-asset-main">
          <label class="exec-label">素材（视频或图片）<select id="exec-asset"><option value="">选择作品</option></select></label>
          <video id="exec-preview" controls preload="metadata" hidden></video>
          <img id="exec-preview-image" alt="图片预览" hidden>
        </div>
      </div>
      <div class="exec-copy-fields">
        <div class="exec-copy-left">
          <label>标题<input id="exec-title" maxlength="20" placeholder="抖音/小红书均不超过 20 字"></label>
          <label>话题标签<input id="exec-tags" placeholder="空格分隔，抖音最多 5 个，小红书最多 10 个"></label>
        </div>
        <div class="exec-copy-right">
          <label>正文 / 作品描述<textarea id="exec-copy-result" rows="5" placeholder="可手写，或点上方 AI 识别视频生成"></textarea></label>
        </div>
      </div>
      <div class="exec-run-row">
        <button id="exec-run-quick" class="exec-primary" type="button">发布到抖音</button>
        <button id="exec-run-xhs" class="exec-primary" type="button">发布到小红书</button>
        <button id="exec-assign" type="button">按手机分配素材</button>
        <span id="exec-assign-status" class="exec-copy-status"></span>
      </div>
    </section>

    <section class="exec-pane" data-pane="control" hidden>
      <div id="exec-transcript" class="exec-write-log"></div>
      <label class="exec-label">任务指令<textarea id="exec-goal" rows="3" placeholder="描述要做什么。发布、发送、删除、付款会先确认。"></textarea></label>
      <div class="exec-run-row">
        <button id="exec-run-task" class="exec-primary" type="button">开始执行</button>
        <button id="exec-stop" class="exec-danger" type="button">停止／人工接管</button>
      </div>
      <p id="exec-help" class="exec-help">普通操作直接执行；发布、发送、删除、付款及敏感设置修改需要确认。</p>
    </section>

    <section class="exec-pane" data-pane="write" hidden>
      <div id="exec-write-log" class="exec-write-log"></div>
      <label class="exec-label">对话修改（每个会话=一套脚本功能，可在此下发、写作、调试）<textarea id="exec-write-input" rows="3" placeholder="例如：把第一步改成点击搜索框，再加一步返回；或：把这套脚本下发到1号手机试跑"></textarea></label>
      <div class="exec-run-row">
        <button id="exec-write-start" class="exec-primary" type="button">开始写作</button>
        <button id="exec-write-send" type="button">发送修改</button>
        <button id="exec-write-save" type="button">保存脚本</button>
        <button id="exec-write-quick" type="button">加入快捷入口</button>
        <button id="exec-write-try" type="button">下发选中手机试跑</button>
      </div>
      <label class="exec-label">结构化草稿<textarea id="exec-draft" rows="10" spellcheck="false"></textarea></label>
      <p class="exec-help">会话式写作：写 → 对话改 → 保存 → 下发试跑 → 贴日志回来再改，同一会话最终形成一套可用脚本。试跑前请先保存。</p>
    </section>

    <section class="exec-pane" data-pane="video" hidden>
      <div class="exec-video-form">
        <label class="exec-label">话题<textarea id="video-topic" rows="2" placeholder="例如：秋天森林里的猴子"></textarea></label>
        <div class="exec-run-row">
          <label class="exec-inline">数量<input id="video-count" type="number" min="1" max="20" value="1"></label>
          <label class="exec-inline">时长(秒)<input id="video-duration" type="number" min="5" max="15" value="10"></label>
          <label class="exec-inline">画质<select id="video-quality"><option value="480p">480P</option><option value="720p" selected>720P</option></select></label>
          <label class="exec-inline">画面比例<select id="video-aspect"><option>1:1</option><option>4:3</option><option>3:4</option><option>9:16</option><option selected>16:9</option></select></label>
          <label class="exec-inline">参考图（可空，最多 9 张）<input id="video-ref" type="file" multiple accept="image/jpeg,image/png,image/webp"></label>
          <button id="video-make" type="button">生成提示词</button>
          <button id="video-doctor" type="button">检测服务</button>
          <span id="video-status" class="exec-copy-status"></span>
        </div>
        <div id="video-thumbs" class="exec-thumbs"></div>
        <div class="exec-run-row">
          <label class="exec-inline" style="flex:1 1 300px;min-width:0">任务号<input id="video-taskid" placeholder="后台看到的任务号，粘贴到这里可回查下载"></label>
          <button id="video-pull" type="button">按任务号拉取</button>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline" style="flex:1 1 320px;min-width:0">下载文件夹<input id="video-output" placeholder="留空则用 Downloads\\FishFleet-视频生成"></label>
          <button id="video-pick" type="button">选择文件夹</button>
          <button id="video-open" type="button">打开文件夹</button>
        </div>
      </div>
      <div id="video-list" class="exec-video-list"></div>
      <div class="exec-run-row"><button id="video-generate" class="exec-primary" type="button" hidden>确认生成（可能产生费用）</button></div>
    </section>

    <section class="exec-pane" data-pane="lead" hidden>
      <div class="exec-lead-form">
        <label class="exec-label">关键词（一行一个，最多 20 个）<textarea id="lead-keywords" rows="3" placeholder="手机主板机&#10;手机维修"></textarea></label>
        <div class="exec-run-row">
          <label class="exec-inline">排序<select id="lead-sort"><option value="">不限</option><option>最新发布</option><option>最多点赞</option><option>最多评论</option><option>最多收藏</option></select></label>
          <label class="exec-inline">发布时间<select id="lead-publish-time"><option value="">不限</option><option>一天内</option><option>一周内</option><option>半年内</option></select></label>
          <label class="exec-inline">视频时长<select id="lead-duration"><option value="">不限</option><option>1分钟以下</option><option>1-5分钟</option><option>5分钟以上</option></select></label>
          <label class="exec-inline">搜索范围<select id="lead-scope"><option value="">不限</option><option>关注的人</option><option>最近看过</option><option>还未看过</option></select></label>
          <label class="exec-inline">位置距离<select id="lead-distance"><option value="">不限</option><option>同城</option></select></label>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline">每个词看视频<input id="lead-videos" type="number" min="1" max="50" value="5"></label>
          <label class="exec-inline">每视频读评论<input id="lead-comments" type="number" min="1" max="200" value="20"></label>
          <label class="exec-inline">回复条数上限<input id="lead-reply-limit" type="number" min="1" max="200" value="20"></label>
          <label class="exec-inline">回复间隔秒<input id="lead-interval" type="number" min="0" max="600" value="0"></label>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline">每操作N次休息<input id="lead-cd-every" type="number" min="1" max="500" value="10"></label>
          <label class="exec-inline">休息秒数<input id="lead-cd-seconds" type="number" min="1" max="3600" value="60"></label>
        </div>
        <div class="exec-run-row" style="align-items:flex-start">
          <div style="flex:1 1 240px;min-width:0">
            <div class="exec-lead-devices-label">采集用手机</div>
            <div id="lead-collect-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </div>
          <div style="flex:1 1 240px;min-width:0">
            <div class="exec-lead-devices-label">回复用手机</div>
            <div id="lead-reply-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </div>
        </div>
        <label class="exec-label">意向关键词（命中才算线索，空格或逗号分隔）<input id="lead-intent" placeholder="多少钱 有偿 哪里买 求推荐 能修吗"></label>
        <label class="exec-label">排除关键词（可选，招聘类默认排除）<input id="lead-exclude" placeholder="招聘 招人 兼职"></label>
        <div class="exec-copy-fields">
          <div class="exec-copy-left">
            <label>自动回复内容（评论区用，可留空；支持 {昵称} {评论}）<textarea id="lead-reply" rows="4" placeholder="你好{昵称}，看到你说的{评论}，可以私信我，我这边能处理"></textarea></label>
          </div>
          <div class="exec-copy-right">
            <label>私信内容（可留空；第一版只生成草稿，不自动发私信）<textarea id="lead-dm" rows="4" placeholder="你好{昵称}，关于{评论}，我这边可以帮你看下"></textarea></label>
          </div>
        </div>
        <div class="exec-run-row">
          <button id="lead-run" class="exec-primary" type="button">开始采集</button>
          <button id="lead-reply" class="exec-primary" type="button">开始回复</button>
          <button id="lead-stop" class="exec-danger" type="button">停止</button>
          <span id="lead-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">① <b>开始采集</b>：只读搜索、看视频读评论，筛出线索写进中转站；<br>
          ② <b>开始回复</b>：从「回复用手机」里领线索，去对方主页核实身份、打开私信、把文案填进输入框，<b>停在发送键前</b>。<br>
          两步完全独立：可以白天采一晚、第二天再统一回复；采集机不用登录，回复机必须登录。<strong>全程不会点发送。</strong></p>
      </div>
      <div id="lead-results" class="exec-lead-results"></div>
    </section>

    <section class="exec-pane" data-pane="xhslead" hidden>
      <div class="exec-lead-form">
        <label class="exec-label">关键词（一行一个，最多 20 个）<textarea id="xhs-keywords" rows="3" placeholder="郑州咖啡&#10;郑州露营"></textarea></label>
        <div class="exec-run-row">
          <label class="exec-inline">排序<select id="xhs-sort" title="小红书排序（综合/最新/最热）"><option value="">综合（默认）</option><option>最新</option><option>最热</option></select></label>
          <label class="exec-inline">每个词看笔记<input id="xhs-videos" type="number" min="1" max="50" value="5"></label>
          <label class="exec-inline">每笔记读评论<input id="xhs-comments" type="number" min="1" max="200" value="20"></label>
          <label class="exec-inline">每笔记翻屏<input id="xhs-screens" type="number" min="1" max="40" value="20"></label>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline">每操作N次休息<input id="xhs-cd-every" type="number" min="1" max="500" value="10"></label>
          <label class="exec-inline">休息秒数<input id="xhs-cd-seconds" type="number" min="1" max="3600" value="60"></label>
        </div>
        <label class="exec-label">意向关键词（命中才算线索，空格或逗号分隔）<input id="xhs-intent" placeholder="多少钱 求推荐 哪里买 能修吗 怎么弄"></label>
        <label class="exec-label">排除关键词（可选，招聘类默认排除）<input id="xhs-exclude" placeholder="招聘 招人 兼职"></label>
        <label class="exec-label">私信内容（支持 {昵称} {评论} {标题}；只生成草稿，不自动发）
          <div class="exec-run-row" style="margin:6px 0">
            <select id="xhs-dm-phrase" style="flex:1 1 220px;min-width:0"><option value="">— 选择常用语 —</option></select>
            <button id="xhs-dm-save" type="button">存为常用语</button>
            <button id="xhs-dm-del" type="button" class="exec-danger">删除常用语</button>
          </div>
          <textarea id="xhs-dm" rows="4" placeholder="你好{昵称}，看到你说{评论}，我这边可以帮你看下"></textarea>
        </label>
        <div class="exec-run-row" style="align-items:flex-start">
          <label class="exec-label" style="flex:1 1 240px;min-width:0">采集用手机（可多选）
            <div id="xhs-collect-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </label>
          <label class="exec-label" style="flex:1 1 240px;min-width:0">回复用手机（可多选）
            <div id="xhs-reply-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </label>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline">回复条数上限<input id="xhs-reply-limit" type="number" min="1" max="200" value="50"></label>
          <label class="exec-inline">每回<input id="xhs-reply-cd-every" type="number" min="1" max="200" value="10">个后冷却<input id="xhs-reply-cd-seconds" type="number" min="0" max="3600" value="60">秒</label>
          <label class="exec-inline">每个回复间隔<input id="xhs-reply-interval" type="number" min="0" max="600" value="8">秒</label>
        </div>
        <div class="exec-run-row">
          <button id="xhs-run" class="exec-primary" type="button">开始采集</button>
          <button id="xhs-reply" class="exec-primary" type="button">开始回复（只填草稿）</button>
          <button id="xhs-stop" class="exec-danger" type="button">停止</button>
          <span id="xhs-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">只读采集：自动搜索、进笔记读评论，命中后<strong>记录对方小红书号</strong>并生成私信草稿；<strong>不在评论区回复</strong>。
          回复阶段只把草稿填进输入框、确认发送键可读，<strong>不会点发送</strong>——首私信对方未回复前只有 1 条配额，由您人工放行。</p>
      </div>
      <div id="xhs-results" class="exec-lead-results"></div>
    </section>

    <section class="exec-pane" data-pane="bilead" hidden>
      <div class="exec-lead-form">
        <label class="exec-label">关键词（一行一个，最多 20 个）<textarea id="bl-keywords" rows="3" placeholder="手机挖矿&#10;挖矿"></textarea></label>
        <div class="exec-run-row">
          <label class="exec-inline">每个词看视频<input id="bl-videos" type="number" min="1" max="50" value="5"></label>
          <label class="exec-inline">每视频读评论<input id="bl-comments" type="number" min="1" max="30" value="10"></label>
          <label class="exec-inline">排序方式
            <select id="bl-sort">
              <option value="默认排序">默认排序（综合）</option>
              <option value="新发布">新发布</option>
              <option value="播放多">播放多</option>
              <option value="弹幕多">弹幕多</option>
            </select>
          </label>
        </div>
        <label class="exec-label">私信内容（支持 {昵称} {评论} {keyword}；只生成草稿，不自动发）
          <div class="exec-run-row" style="margin:6px 0">
            <select id="bl-dm-phrase" style="flex:1 1 220px;min-width:0"><option value="">— 选择常用语 —</option></select>
            <button id="bl-dm-save" type="button">存为常用语</button>
            <button id="bl-dm-del" type="button" class="exec-danger">删除常用语</button>
          </div>
          <textarea id="bl-dm" rows="4" placeholder="你好呀，看到你在「{keyword}」相关视频下留言「{评论}」，我这边刚好懂这块，有问题可以随时问我～"></textarea>
        </label>
        <div class="exec-run-row" style="align-items:flex-start">
          <label class="exec-label" style="flex:1 1 240px;min-width:0">采集用手机（可多选）
            <div id="bl-collect-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </label>
          <label class="exec-label" style="flex:1 1 240px;min-width:0">回复用手机（可多选）
            <div id="bl-reply-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </label>
        </div>
        <div class="exec-run-row">
          <label class="exec-inline">回复条数上限<input id="bl-reply-limit" type="number" min="1" max="200" value="50"></label>
          <label class="exec-inline">每条间隔(秒)<input id="bl-interval" type="number" min="0" max="600" value="5"></label>
          <label class="exec-inline">每<input id="bl-cool-every" type="number" min="1" max="500" value="10">条冷却</label>
          <label class="exec-inline"><input id="bl-cool-sec" type="number" min="0" max="3600" value="60">秒</label>
        </div>
        <div class="exec-run-row">
          <button id="bl-run" class="exec-primary" type="button">开始采集</button>
          <button id="bl-reply" class="exec-primary" type="button">开始回复</button>
          <button id="bl-stop" class="exec-danger" type="button">停止</button>
          <span id="bl-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">只读采集：搜索关键词 → 进视频读评论 → 点开评论者主页取 <strong>UID</strong> → 写入中转站。
          <strong>不在评论区回复，也不发私信</strong>；后续换号用 <code>bilibili://space/&lt;uid&gt;</code> 联系，
          私信会话里对方未回复前<strong>只能发 1 条</strong>。</p>
      </div>
      <div id="bl-results" class="exec-lead-results"></div>
    </section>

    <section class="exec-pane" data-pane="bili" hidden>
      <div class="exec-lead-form">
        <div class="exec-run-row">
          <label class="exec-inline" style="flex:2 1 260px;min-width:0">用相册第几个素材
            <select id="bili-album">
              <option value="0">第 1 个（最新）</option>
              <option value="1">第 2 个</option>
              <option value="2">第 3 个</option>
              <option value="3">第 4 个</option>
              <option value="4">第 5 个</option>
            </select>
          </label>
          <label class="exec-inline" style="flex:2 1 260px;min-width:0">AI 识别参考素材
            <select id="bili-ref"><option value="">不生成，手写文案</option></select>
          </label>
          <button id="bili-copy" type="button">AI 生成文案</button>
          <span id="bili-copy-status" class="exec-copy-status"></span>
        </div>
        <div class="exec-copy-fields">
          <div class="exec-copy-left">
            <label>视频标题（≤80 字）<input id="bili-title" maxlength="80" placeholder="不超过 80 字"></label>
          </div>
          <div class="exec-copy-right">
            <label>简介（≤2000 字，可空）<textarea id="bili-desc" rows="5" placeholder="可手写，或选参考素材后点“AI 生成文案”"></textarea></label>
          </div>
        </div>
        <div class="exec-run-row">
          <button id="bili-run" class="exec-primary" type="button">开始发布到B站</button>
          <span id="bili-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">发布链：首页「+」→ 相册选素材 → 去编辑 → 编辑器「下一步」→ 投稿页填标题／简介 → 发布。
          <strong>素材取自手机相册本身</strong>（按「最近项目」倒序），不走「导入视频」。
          发布后请到「我的 → 稿件管理」核验是否出现该标题。</p>
      </div>
      <div id="bili-results"></div>
    </section>

    <!-- ===== 发布视频（三平台合并，顶部选平台） ===== -->
    <section class="exec-pane" data-pane="publish" hidden>
      <div class="exec-platform-tabs" id="pub-platform-tabs">
        <button type="button" data-platform="douyin" class="on">抖音</button>
        <button type="button" data-platform="xiaohongshu">小红书</button>
        <button type="button" data-platform="bilibili">B站</button>
        <button type="button" data-platform="tiktok">TikTok</button>
        <button type="button" data-platform="facebook">Facebook</button>
      </div>
      <div class="exec-asset-block">
        <div class="exec-asset-actions">
          <button id="pub-upload" type="button">导入素材</button>
          <button id="pub-upload-dir" type="button">导入文件夹</button>
          <select id="pub-library" title="从素材库挑选加入本次发布（只加选中的）"><option value="">从素材库选择…</option></select>
          <button id="pub-library-add" type="button">加入</button>
          <input id="pub-file" type="file" multiple accept="video/mp4,video/quicktime,video/webm,.mkv,image/jpeg,image/png,image/webp" hidden>
          <input id="pub-file-dir" type="file" webkitdirectory directory multiple accept="video/mp4,video/quicktime,video/webm,.mkv,image/jpeg,image/png,image/webp" hidden>
          <button id="pub-copy" type="button">AI 生成文案</button>
          <span id="pub-copy-status" class="exec-copy-status"></span>
        </div>
        <div id="pub-assets" class="exec-result-grid"></div>
        <div class="exec-run-row">
          <button id="pub-assign" type="button">按手机分配素材</button>
          <span id="pub-assign-status" class="exec-copy-status"></span>
        </div>
        <div id="pub-assign-panel" class="pub-assign-panel" hidden></div>
      </div>
      <div class="exec-copy-fields">
        <div class="exec-copy-left">
          <label>标题<input id="pub-title" placeholder="选平台后按该平台上限校验"></label>
          <label id="pub-album-wrap" hidden>用相册第几个素材
            <select id="pub-album">
              <option value="0">第 1 个（最新）</option>
              <option value="1">第 2 个</option>
              <option value="2">第 3 个</option>
              <option value="3">第 4 个</option>
              <option value="4">第 5 个</option>
            </select>
          </label>
        </div>
        <div class="exec-copy-right">
          <label id="pub-desc-label">正文 / 作品描述<textarea id="pub-desc" rows="5" placeholder="可手写，或点「AI 生成文案」"></textarea></label>
        </div>
      </div>
      <label id="pub-tags-wrap">话题标签<input id="pub-tags" placeholder="空格分隔"></label>
      <div class="exec-run-row">
        <button id="pub-run" class="exec-primary" type="button">发布</button>
        <button id="pub-stop" class="exec-danger" type="button">停止</button>
        <span id="pub-status" class="exec-copy-status"></span>
      </div>
      <p id="pub-help" class="exec-help"></p>
    </section>

    <!-- ===== 截流（三平台合并，顶部选平台） ===== -->
    <section class="exec-pane" data-pane="leadx" hidden>
      <div class="exec-platform-tabs" id="lead-platform-tabs">
        <button type="button" data-platform="douyin" class="on">抖音</button>
        <button type="button" data-platform="xiaohongshu">小红书</button>
        <button type="button" data-platform="bilibili">B站</button>
        <button type="button" data-platform="tiktok">TikTok</button>
        <button type="button" data-platform="facebook">Facebook</button>
      </div>
      <div class="exec-lead-form">
        <label class="exec-label">关键词（一行一个，最多 20 个）<textarea id="leadx-keywords" rows="3" placeholder="手机主板机&#10;手机维修"></textarea></label>
        <div class="exec-run-row">
          <label class="exec-inline">排序方式<select id="leadx-sort"><option value="">默认排序</option></select></label>
          <label class="exec-inline">只收X天内评论<input id="leadx-days" type="number" min="0" max="365" value="0" title="0=不限；以任务下发时间为基准换算相对时间"></label>
          <label class="exec-inline">采集评论总数<input id="leadx-target" type="number" min="1" max="20000" value="200"></label>
          <span class="exec-side-note" id="leadx-hint">跑到累计命中这么多条线索就停；不填数字 = 按平台上限跑完。</span>
        </div>
        <label class="exec-label">意向关键词（可选，留空 = 全量收录）<input id="leadx-intent" placeholder="多少钱 有偿 哪里买 求推荐"></label>
        <label class="exec-label">排除关键词（可选）<input id="leadx-exclude" placeholder="招聘 招人 兼职"></label>
        <div class="exec-run-row" style="align-items:flex-start">
          <div style="flex:1 1 260px;min-width:0">
            <div class="exec-lead-devices-label" id="leadx-collect-label">采集用手机</div>
            <div id="leadx-collect-devices" class="exec-phone-grid" style="margin-top:4px"></div>
          </div>
          <div style="flex:1 1 260px;min-width:0">
            <div class="exec-lead-devices-label" id="leadx-reply-label">回复用手机（回复时选）</div>
            <div id="leadx-reply-devices" class="exec-phone-grid" style="margin-top:4px"></div>
            <button id="leadx-copy-phones" type="button" class="leadx-copy-phones">采集用手机 → 回复用手机</button>
          </div>
        </div>
        <label class="exec-label">私信内容（回复时必填；支持 {昵称} {评论} {keyword}；只填草稿不发送）
          <textarea id="leadx-dm" rows="3" placeholder="你好{昵称}，看到你在评论区提到「{评论}」，我这边可以帮你看下"></textarea>
        </label>
        <div class="exec-run-row">
          <label class="exec-inline">回复条数上限<input id="leadx-reply-limit" type="number" min="1" max="200" value="10"></label>
          <label class="exec-inline">每条间隔秒<input id="leadx-interval" type="number" min="0" max="600" value="0"></label>
        </div>
        <div class="exec-run-row">
          <button id="leadx-run" class="exec-primary" type="button">开始采集</button>
          <button id="leadx-reply" class="exec-primary" type="button">开始回复（只填草稿）</button>
          <button id="leadx-stop" class="exec-danger" type="button">停止</button>
          <span id="leadx-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">① <b>开始采集</b>：只读搜索、读评论，命中写进中转站，<strong>不需要填回复内容</strong>；<br>
          ② <b>开始回复</b>：独立动作，从「回复用手机」领线索去对方主页填草稿，<strong>停在发送键前</strong>；<br>
          两步互不影响，也可以在执行记录里对历史采集任务单独点「回复」。</p>
      </div>
      <div id="leadx-results" class="exec-lead-results"></div>
    </section>

    <!-- ===== 回复（2026-11-06 从「截流」拆出来的独立面板：选批次回复） =====
         主人反馈：FB/TK 采集完了却没有回复入口。根因是回复按钮一直嵌在截流面板里，
         而且是从整个池子盲领，没法"只回某一批"。这里独立成面板，先选批次再回复。 -->
    <section class="exec-pane" data-pane="replypane" hidden>
      <div class="exec-platform-tabs" id="reply-platform-tabs">
        <button type="button" data-platform="douyin" class="on">抖音</button>
        <button type="button" data-platform="xiaohongshu">小红书</button>
        <button type="button" data-platform="bilibili">B站</button>
        <button type="button" data-platform="tiktok">TikTok</button>
        <button type="button" data-platform="facebook">Facebook</button>
      </div>
      <div class="exec-lead-form">
        <div class="exec-run-row">
          <button id="reply-refresh" type="button">刷新批次</button>
          <label class="exec-inline">最近天数<input id="reply-days" type="number" min="1" max="365" value="60"></label>
          <span id="reply-batch-status" class="exec-copy-status"></span>
        </div>
        <div class="exec-lead-devices-label">采集批次（日期 + 关键词，点一行选中；只回这一批）</div>
        <div id="reply-batches" class="exec-lead-results" style="max-height:230px;overflow:auto;margin-top:4px"></div>
        <div class="exec-lead-devices-label" style="margin-top:8px">回复用手机</div>
        <div id="reply-devices" class="exec-phone-grid" style="margin-top:4px"></div>
        <label class="exec-label">私信内容（支持 {昵称} {评论} {keyword}；只填草稿不发送）
          <textarea id="reply-dm" rows="3" placeholder="你好{昵称}，看到你在评论区提到「{评论}」，我这边可以帮你看下"></textarea>
        </label>
        <div class="exec-run-row">
          <label class="exec-inline">回复条数上限<input id="reply-limit" type="number" min="1" max="200" value="10"></label>
          <label class="exec-inline">每条间隔秒<input id="reply-interval" type="number" min="0" max="600" value="0"></label>
        </div>
        <div class="exec-run-row">
          <button id="reply-run" class="exec-primary" type="button">开始回复（只填草稿）</button>
          <button id="reply-stop" class="exec-danger" type="button">停止</button>
          <span id="reply-status" class="exec-copy-status"></span>
        </div>
        <p class="exec-help">① 先选平台，再点「刷新批次」，列表按<b>采集日期 + 关键词</b>分组；<br>
          ② 选中一行 = 只回这一批的线索，不会碰到别的批次；<br>
          ③ 回复只填草稿、<b>停在发送键前</b>，不会真的发出去。</p>
      </div>
      <div id="reply-results" class="exec-lead-results"></div>
    </section>

    <!-- ===== 手机一键（上方选手机，下方按分组执行预制动作） ===== -->
    <section class="exec-pane" data-pane="onekey" hidden>
      <div class="exec-lead-form">
        <div class="exec-lead-devices-label">选择手机（可多选）</div>
        <div id="onekey-devices" class="exec-phone-grid" style="margin:4px 0 8px"></div>
        <div class="exec-run-row">
          <input id="onekey-search" placeholder="搜索动作，如 亮度/重启/输入法" style="flex:1 1 200px;height:30px;background:#0e1218;border:1px solid #2a2f3a;border-radius:6px;color:#dde;padding:0 10px">
          <button id="onekey-reload" type="button">刷新动作</button>
          <button id="onekey-selfcheck" type="button" title="核对这台机器的真实坐标，偏了就按机型自动修正">🔍 一键自检</button>
          <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:#9ab"><input id="onekey-selfcheck-nav" type="checkbox" checked>自动回首页</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:#9ab"><input id="onekey-selfcheck-fix" type="checkbox" checked>自动修复</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:#9ab"><input id="onekey-selfcheck-all" type="checkbox">全部App</label>
          <label style="display:flex;align-items:center;gap:4px;font-size:12px;color:#f0b060" title="会force-stop应用，可能让小红书等回到登录页"><input id="onekey-selfcheck-hard" type="checkbox">强制重启App</label>
          <button id="onekey-onboard" type="button" title="新机接入：几何建档→全应用自检→各平台发布/采集模拟→视觉确认偏移→自动修正→报告。LLM只在这里用。">🧪 新机模拟测试</button>
          <span id="onekey-status" class="exec-copy-status"></span>
        </div>
        <div id="onekey-selfcheck-out" style="display:none;margin-top:8px"></div>
        <div id="onekey-onboard-out" style="display:none;margin-top:8px"></div>
        <div id="onekey-groups" style="display:flex;flex-direction:column;gap:10px;margin-top:8px"></div>
        <p class="exec-help">上方先勾手机，下方点动作即执行；<b style="color:#f08080">红色高危动作要点两次确认</b>。默认覆盖：回桌面/截图/锁竖屏/保持唤醒/熊猫键盘等常用项。</p>
      </div>
      <div id="onekey-results" class="exec-lead-results"></div>
    </section>

    <!-- ===== 执行记录详情（中间区域展示，所有操作都在这里） ===== -->
    <section class="exec-pane" data-pane="detail" hidden>
      <div class="exec-detail-head">
        <strong id="detail-title">任务详情</strong>
        <span id="detail-sub" class="exec-copy-status"></span>
      </div>
      <div class="exec-run-row" id="detail-actions"></div>
      <div id="detail-tasks" class="exec-detail-tasks"></div>
      <div class="exec-detail-log-head">执行日志</div>
      <div id="detail-events" class="exec-detail-events"></div>
    </section>

    <div id="exec-feedback" class="exec-feedback" role="status" aria-live="polite">就绪</div>
    <div id="exec-progress" class="exec-progress" role="status"></div>
    <div id="exec-plan" class="exec-plan"></div>
    <div id="exec-results" class="exec-results"></div>
  </main>
  <aside class="exec-observe">
    <strong>实时画面</strong>
    <label class="exec-observe-inline" style="margin:8px 0">观察手机
      <select id="exec-device"><option value="">选择手机</option></select>
    </label>
    <div id="exec-observe-empty">选一台手机，右侧会直接显示实时大屏</div>
    <img id="exec-image" alt="观察画面" hidden>
    <div id="exec-shot-info">未捕捉画面</div>
  </aside>
</div>
</div>`;

const $=id=>document.getElementById(id);
const QUICK_SKILL='builtin:douyin-publish';
const LEAD_SKILL='builtin:douyin-lead';
const XHS_LEAD_SKILL='builtin:xiaohongshu-lead';
const XHS_DM_SKILL='builtin:xiaohongshu-dm';
const BILI_SKILL='builtin:bilibili-publish';
const BILI_LEAD_SKILL='builtin:bilibili-lead';
const XHS_SKILL='builtin:xiaohongshu-publish';
const XHS_TITLE_LIMIT=20;   // 实测：小红书标题 20 字，超出会被输入框静默截断
const XHS_BODY_LIMIT=6000;  // 实测写入 6100 字仍未截断，取 6000 作安全值
const XHS_TAG_LIMIT=10;
const STATE_NAMES={queued:'排队中',observing:'正在截图',thinking:'AI 分析中',awaiting_approval:'等待确认',checking:'核对画面',executing:'执行中',stopping:'停止中',completed:'已完成',stopped:'已停止',failed:'失败',needs_review:'待人工核验',interrupted:'重启中断'};
const FINISHED=['completed','stopped','failed','needs_review','interrupted'];

const state={mode:'quick',quickView:'publish',devices:[],selected:new Set(),assignments:new Map(),copies:new Map(),tasks:[],timer:null,draft:null,writeHistory:[],pending:new Map(),busy:false,savedId:null,scripts:[],taskMode:'control'};
let quickHistoryRows=[];
let quickHistoryOpen=false;
let quickRecordId=null;
let quickScripts=[];
try{quickScripts=JSON.parse(localStorage.getItem('ff_quick_scripts')||'[]');if(!Array.isArray(quickScripts))quickScripts=[];}catch(e){quickScripts=[];}

// 提示按界面分开保存：A 界面产生的结果不会渲染到 B 界面，切回来还能看到自己那条。
const FB={quick:{t:'',k:''},control:{t:'',k:''},write:{t:'',k:''},video:{t:'',k:''}};
function paintFeedback(){
  const el=$('exec-feedback');if(!el)return;
  const f=FB[state.mode]||{t:'',k:''};
  el.textContent=f.t;el.className='exec-feedback'+(f.k?' '+f.k:'');
}
function feedback(text,kind,owner){
  const m=owner||state.mode;
  if(!FB[m])FB[m]={t:'',k:''};
  FB[m]={t:String(text||''),k:kind||''};
  if(m===state.mode)paintFeedback();
}
function ok(text,owner){feedback(text,'ok',owner);}
function log(text){
  const el=$('exec-log');if(el)el.textContent+=text+'\n';
  feedback(text,/失败|错误|不可用|请选择|请先|不合法/.test(text)?'err':'');
}
async function api(path,body){
  const opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const r=await fetch(path,opt);let v={};
  try{v=await r.json();}catch(e){}
  if(!r.ok){
    const d=v.detail;
    throw Error(typeof d==='string'?d:(d&&JSON.stringify(d))||v.error||('请求失败 '+r.status));
  }
  return v;
}
async function apiDelete(path){
  const r=await fetch(path,{method:'DELETE'});
  let v={};try{v=await r.json();}catch(e){}
  if(!r.ok){
    const d=v.detail;
    throw Error(typeof d==='string'?d:(d&&JSON.stringify(d))||v.error||('请求失败 '+r.status));
  }
  return v;
}
function option(sel,value,label){const o=document.createElement('option');o.value=value;o.textContent=label;sel.append(o);return o;}
function deviceOf(serial){return state.devices.find(d=>d.serial===serial);}
function numberLabel(serial){const d=deviceOf(serial);return d?d.index+'号':String(serial).slice(-6);}

/* ---------- 左侧栏：按模式切换 ---------- */
const SESSION_KEYS={control:'ff_ai_sessions_v2',write:'ff_write_sessions_v1'};
function loadSessions(mode){
  try{const v=JSON.parse(localStorage.getItem(SESSION_KEYS[mode])||'[]');return Array.isArray(v)?v:[];}catch(e){return [];}
}
function storeSessions(mode,list){
  try{localStorage.setItem(SESSION_KEYS[mode],JSON.stringify(list.slice(-40)));}catch(e){}
}
function sessions(mode){return loadSessions(mode);}
function currentSession(){
  const list=sessions(state.mode);
  return list.find(s=>s.id===state.sessionId)||null;
}
function newSession(mode){
  const list=sessions(mode);
  const s={id:(crypto.randomUUID?crypto.randomUUID():String(Date.now())),title:mode==='write'?'新写作':'新会话',messages:[],history:[],draft:null,goal:'',taskIds:[]};
  list.push(s);storeSessions(mode,list);
  state.sessionId=s.id;
  if(mode==='write'){state.writeHistory=[];setDraft(null);renderWrite();}
  else{$('exec-goal').value='';state.tasks=[];stopPolling();clearTaskAreas();renderTranscript();renderSide();}
  renderSide();ok('已新建会话');
}
function switchSession(mode,id){
  const list=sessions(mode);
  const s=list.find(x=>x.id===id);
  if(!s)return;
  state.sessionId=id;
  state.tasks=[];stopPolling();clearTaskAreas();
  if(mode==='write'){
    state.writeHistory=(s.history||[]).slice();
    setDraft(s.draft||null);
    renderWrite();
  }else{
    $('exec-goal').value=s.goal||'';
    state.tasks=(s.taskIds||[]).slice();
    renderTranscript();
    if(state.tasks.length)poll();
  }
  renderSide();
}
function renderTranscript(){
  const box=$('exec-transcript');if(!box)return;
  box.replaceChildren();
  const s=currentSession();
  const msgs=(s&&s.messages)||[];
  if(!msgs.length){
    const tip=document.createElement('div');tip.className='exec-side-note';
    tip.textContent='选择手机后输入任务指令，结果与确认请求会显示在这里。';
    box.append(tip);return;
  }
  for(const m of msgs){
    const d=document.createElement('div');d.className='exec-bubble '+m.role;
    const who=document.createElement('strong');who.textContent=m.role==='user'?'我':'执行助手';
    const body=document.createElement('div');body.textContent=m.text;
    d.append(who,body);box.append(d);
  }
  box.scrollTop=box.scrollHeight;
}
function message(role,text){
  const list=sessions(state.mode);
  const i=list.findIndex(s=>s.id===state.sessionId);
  if(i<0)return;
  list[i].messages=list[i].messages||[];
  list[i].messages.push({role,text:String(text).slice(0,4000)});
  list[i].messages=list[i].messages.slice(-80);
  if(role==='user'&&list[i].title==='新会话')list[i].title=String(text).slice(0,16);
  storeSessions(state.mode,list);
  if(state.mode==='control')renderTranscript();
}
function quickEntries(){
  const box=document.createElement('div');box.className='exec-quick-list';
  const add=(label,sub,active,onclick)=>{
    const b=document.createElement('button');b.type='button';b.className='exec-quick-item'+(active?' on':'');
    const t=document.createElement('strong');t.textContent=label;
    const s=document.createElement('small');s.textContent=sub;
    b.append(t,s);b.onclick=onclick;box.append(b);
  };
  // 只保留三个入口：发布视频 / 截流 / 手机一键。各自顶部再选平台（2026-11-03 调整）。
  const v=state.quickView;
  add('发布视频','选平台 · 选手机 · 选素材 · 一键发布',v==='publish',()=>{state.quickView='publish';setMode('quick');newPublishTask();});
  add('截流','选平台 · 关键词 · 采集评论',v==='leadx',()=>{state.quickView='leadx';setMode('quick');openLeadPane();});
  // 2026-11-06：回复独立成入口（原来只在截流面板里，FB/TK 采完找不到回复入口）
  add('回复','选平台 · 选采集批次 · 只填草稿',v==='replypane',()=>{state.quickView='replypane';setMode('quick');openReplyPane();});
  add('手机一键','选手机 · 预制动作 · 批量执行',v==='onekey',()=>{state.quickView='onekey';setMode('quick');openOnekey();});
  for(const s of state.scripts){
    if(!quickScripts.includes(s.id))continue;
    add(s.name,'已保存脚本 · 一键执行',false,()=>{setMode('control');runScript(s.id,{id:s.id,name:s.name});});
  }
  return box;
}
function renderSide(){
  const box=$('exec-side');if(!box)return;
  box.replaceChildren();
  const head=document.createElement('div');head.className='exec-side-title';
  const m=state.mode;
  if(m==='quick'){
    head.textContent='快捷入口';box.append(head,quickEntries());
    const history=document.createElement('details');history.className='exec-quick-history';history.open=quickHistoryOpen;
    history.addEventListener('toggle',()=>{quickHistoryOpen=history.open;});
    const summary=document.createElement('summary');summary.textContent='执行记录（'+quickHistoryRows.length+'）';history.append(summary);
    const list=document.createElement('div');list.className='exec-session-list';
    for(const row of quickHistoryRows){
      const button=document.createElement('button');button.type='button';button.className='exec-record'+(quickRecordId===row.id?' active':'');
      const title=document.createElement('strong');title.textContent=row.name;
      const sub=document.createElement('small');sub.textContent=new Date(row.created*1000).toLocaleString()+' · '+row.tasks.map(t=>numberLabel(t.serial)+' '+(STATE_NAMES[t.status]||t.status)).join('、');
      button.append(title,sub);button.onclick=()=>openDetail(row);list.append(button);
    }
    if(!quickHistoryRows.length){const tip=document.createElement('div');tip.className='exec-side-note';tip.textContent='暂无执行记录';list.append(tip);}
    history.append(list);box.append(history);
    return;
  }
  if(m==='video'){
    head.textContent='生成记录';box.append(head);
    const add=document.createElement('button');
    add.type='button';add.className='exec-side-add';add.textContent='＋ 新增任务';
    add.onclick=()=>newVideoProject();
    box.append(add);
    const list=document.createElement('div');list.className='exec-session-list';
    list.textContent='加载中…';box.append(list);
    loadRecords(list);
    return;
  }
  head.textContent=m==='control'?'会话':'写作会话';box.append(head);
  const add=document.createElement('button');add.type='button';add.className='exec-side-add';
  add.textContent='＋ 新建会话';add.onclick=()=>newSession(m);box.append(add);
  const list=document.createElement('div');list.className='exec-session-list';
  for(const s of sessions(m).slice().reverse()){
    const row=document.createElement('div');row.className='exec-session-row'+(s.id===state.sessionId?' active':'');
    const open=document.createElement('button');open.type='button';open.className='exec-session-open';
    open.textContent=s.title||'未命名';open.title=s.title||'';
    open.onclick=()=>switchSession(m,s.id);
    const del=document.createElement('button');del.type='button';del.className='exec-session-del';del.textContent='✕';
    del.title='删除此会话记录（不影响手机上已执行的动作）';
    del.onclick=(e)=>{e.stopPropagation();
      const all=sessions(m).filter(x=>x.id!==s.id);storeSessions(m,all);
      // 删除会话要把它名下的任务进度一起清掉，否则旧进度会一直挂着。
      state.tasks=[];stopPolling();clearTaskAreas();
      if(state.sessionId===s.id){state.sessionId=(all[all.length-1]||{}).id||null;}
      renderSide();
    };
    row.append(open,del);list.append(row);
  }
  if(!sessions(m).length){const tip=document.createElement('div');tip.className='exec-side-note';tip.textContent='还没有会话';list.append(tip);}
  box.append(list);
}
async function refreshQuickHistory(){
  try{
    const response=await api('/api/execution/tasks');
    const groups=new Map();
    for(const t of response.tasks||[]){
      const entry=t.quick_entry_id
        ||(t.script_id===QUICK_SKILL?QUICK_SKILL:(t.script_id===XHS_SKILL?XHS_SKILL:''));
      if(!entry)continue;
      const id=t.quick_batch_id||t.id;
      if(!groups.has(id))groups.set(id,{id,name:t.quick_entry_name||(entry===QUICK_SKILL?'抖音视频发布':(entry===XHS_SKILL?'小红书发布':(t.goal||'快捷任务'))),created:t.created_at||0,tasks:[]});
      groups.get(id).tasks.push(t);
    }
    quickHistoryRows=[...groups.values()].sort((a,b)=>b.created-a.created);
    if(state.mode==='quick')renderSide();
  }catch(e){if(state.mode==='quick')feedback('读取执行记录失败：'+e.message,'err','quick');}
}
function openQuickRecord(row){
  quickRecordId=row.id;state.taskMode='quick';state.tasks=row.tasks.map(t=>t.id);
  // 截流记录看线索卡，发布记录看任务进度。
  state.quickView=recordIsLead(row)?'leadx':'publish';
  stopPolling();clearTaskAreas();renderSide();setMode('quick');poll();
  feedback('已打开执行记录：'+row.name+'；不会重新执行','', 'quick');
}
const LEAD_SKILLS=[LEAD_SKILL,XHS_LEAD_SKILL,BILI_LEAD_SKILL];
function recordIsLead(row){return (row.tasks||[]).some(t=>LEAD_SKILLS.includes(t.script_id));}
function recordLeadTask(row){return (row.tasks||[]).find(t=>LEAD_SKILLS.includes(t.script_id));}

/* ---------- 执行记录详情：中间区域展示，所有操作都在这里 ---------- */
let detailRow=null,detailTimer=null;
function stopDetailTimer(){if(detailTimer){clearTimeout(detailTimer);detailTimer=null;}}
function openDetail(row){
  detailRow=row;quickRecordId=row.id;
  state.quickView='detail';
  setMode('quick');
  stopDetailTimer();
  renderDetail();   // 先画快照，refreshDetail 拉到含确认令牌的详情后重画
  refreshDetail();
}
function openQuickRecord(row){openDetail(row);}
async function refreshDetail(){
  if(!detailRow)return;
  try{
    // 列表接口会剥掉 pending（确认令牌），详情必须按任务逐个拉全量，否则确认键出不来
    const openIds=(detailRow.tasks||[]).filter(t=>!FINISHED.includes(t.status)).map(t=>t.id);
    if(openIds.length){
      const fresh=await Promise.all(openIds.map(id=>api('/api/execution/tasks/'+id).catch(e=>null)));
      const byId=new Map();
      for(const t of fresh){if(t&&t.id)byId.set(t.id,t);}
      detailRow={...detailRow,tasks:detailRow.tasks.map(t=>(byId.get(t.id)?{...t,...byId.get(t.id)}:t))};
    }
    renderDetail();
  }catch(e){renderDetail();}
  const active=(detailRow.tasks||[]).some(t=>!FINISHED.includes(t.status));
  if(active)detailTimer=setTimeout(refreshDetail,2000);
}
function renderDetail(){
  const row=detailRow;if(!row)return;
  const head=$('detail-title'),sub=$('detail-sub');
  if(head)head.textContent=row.name;
  const when=new Date(row.created*1000).toLocaleString();
  if(sub){sub.textContent=when+' · '+row.tasks.map(t=>numberLabel(t.serial)+' '+(STATE_NAMES[t.status]||t.status)).join('、');}

  // 操作区：所有按钮都在中间，记录列表里不再放小按钮
  const bar=$('detail-actions');bar.replaceChildren();
  const mk=(label,css,fn)=>{const b=document.createElement('button');b.type='button';b.className=css||'';b.textContent=label;b.onclick=fn;bar.append(b);return b;};
  mk('← 返回','',()=>{
    stopDetailTimer();
    const sid=((row.tasks||[])[0]||{}).script_id||'';
    const dmLike=['builtin:douyin-dm','builtin:xiaohongshu-dm','builtin:bilibili-dm','builtin:tiktok-dm','builtin:facebook-dm'];
    if(LEAD_SKILLS.includes(sid)||dmLike.includes(sid)){
      state.quickView='leadx';
      state.leadxPlatform=sid.includes('xiaohongshu')?'xiaohongshu':(sid.includes('bilibili')?'bilibili':'douyin');
    }else{
      state.quickView='publish';
    }
    setMode('quick');
  });
  const running=row.tasks.some(t=>!FINISHED.includes(t.status));
  if(running)mk('停止','exec-danger',async()=>{
    for(const t of row.tasks){if(FINISHED.includes(t.status))continue;try{await api('/api/execution/tasks/'+t.id+'/stop',{});}catch(e){}}
    ok('已请求停止；已发出的动作不能撤回');refreshDetail();
  });
  if(recordIsLead(row))mk('开始回复','exec-primary',()=>{const t=recordLeadTask(row);stopDetailTimer();prefillReply(t,row);});
  if(recordIsLead(row))mk('再次采集','',async()=>{
    const t=recordLeadTask(row);
    try{
      const live=(await api('/api/execution/tasks')).tasks||[];
      const busy=live.find(x=>x.serial===t.serial&&!FINISHED.includes(x.status));
      if(busy)throw Error(numberLabel(t.serial)+' 还有任务在跑，等它结束再激活');
      const lead=t.lead||{};
      const r=await api('/api/execution/tasks',{
        serial:t.serial,goal:'再次采集：'+String((lead.keywords||[]).slice(0,3).join('、')||'同配置'),
        script_id:t.script_id,quick_entry_id:t.quick_entry_id||t.script_id,
        quick_entry_name:t.quick_entry_name||'再次采集',
        quick_batch_id:(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random()),
        lead});
      await refreshQuickHistory();
      const newRow=quickHistoryRows.find(x=>x.tasks.some(y=>y.id===r.id));
      if(newRow)openDetail(newRow);
      ok('已按原配置重新下发采集任务');
    }catch(e){feedback('再次采集失败：'+e.message,'err','quick');}
  });
  mk('删除','exec-danger',async()=>{
    if(!window.confirm('删除这条执行记录？（不影响手机上已完成的动作）'))return;
    let removed=0;
    for(const t of row.tasks){try{await apiDelete('/api/execution/tasks/'+t.id);removed++;}catch(e){}}
    stopDetailTimer();detailRow=null;quickRecordId=null;
    await refreshQuickHistory();
    state.quickView='publish';setMode('quick');
    ok(removed?'已删除 '+removed+' 条记录（运行中的需先停止）':'删除失败：任务可能还在运行，请先停止');
  });
  // 断点续接开关（执行记录里手动开关，不在新建区）
  {
    const wrap=document.createElement('label');wrap.className='exec-inline';wrap.title='重启后自动续接没跑完的回复任务（按任务单独开关）';
    const cb=document.createElement('input');cb.type='checkbox';
    const first=row.tasks[0]||{};
    cb.checked=first.resumeable!==false;
    const tx=document.createElement('span');tx.textContent=' 重启后自动续接';
    cb.onchange=async()=>{
      const on=cb.checked;
      try{
        for(const t of row.tasks){await api('/api/execution/tasks/'+t.id+'/resumeable',{resumeable:on});t.resumeable=on;}
        ok(on?'已开启此记录的断点续接':'已关闭此记录的断点续接','', 'quick');
      }catch(e){cb.checked=!on;feedback('开关保存失败：'+e.message,'err','quick');}
    };
    wrap.append(cb,tx);bar.append(wrap);
  }

  // 任务列表：状态 + 失败原因 + 待确认按钮
  const box=$('detail-tasks');box.replaceChildren();
  for(const t of row.tasks){
    const card=document.createElement('div');card.className='exec-detail-task';
    const line=document.createElement('div');line.className='exec-detail-task-line';
    const who=document.createElement('strong');who.textContent=numberLabel(t.serial);
    const st=document.createElement('span');st.className='exec-detail-state exec-state-'+t.status;st.textContent=STATE_NAMES[t.status]||t.status;
    line.append(who,st);
    card.append(line);
    if(t.failure_reason){
      const r=document.createElement('div');r.className='exec-detail-reason';r.textContent='失败原因：'+t.failure_reason;card.append(r);
    }
    // 结束原因（采集目标达成/未达成、卡片耗尽等）始终展示，状态透明
    if(t.end_reason){
      const r=document.createElement('div');r.className='exec-detail-endreason';r.textContent='结束原因：'+t.end_reason;card.append(r);
    }
    if(t.pending&&t.status==='awaiting_approval'){
      const c=document.createElement('div');c.className='exec-pending';
      const txt=document.createElement('span');txt.textContent=t.pending.reason||'需要确认';
      const yes=document.createElement('button');yes.type='button';yes.className='exec-primary';yes.textContent='确认';
      yes.onclick=async()=>{
        yes.disabled=true;
        try{
          const latest=await api('/api/execution/tasks/'+t.id);
          if(!latest.pending)throw Error('本步已变化，请等待刷新');
          await api('/api/execution/tasks/'+t.id+'/approve',{id:latest.pending.id});
          ok(numberLabel(t.serial)+' 已确认');refreshDetail();
        }catch(e){feedback('确认失败：'+e.message,'err','quick');yes.disabled=false;}
      };
      const no=document.createElement('button');no.type='button';no.textContent='取消发布';
      no.title='停掉本机这条任务手机停在当前页不动';
      no.onclick=async()=>{
        no.disabled=true;yes.disabled=true;
        try{
          await api('/api/execution/tasks/'+t.id+'/stop',{});
          ok(numberLabel(t.serial)+' 已取消发布');refreshDetail();
        }catch(e){feedback('取消失败：'+e.message,'err','quick');no.disabled=false;yes.disabled=false;}
      };
      c.append(txt,yes,no);card.append(c);
    }
    box.append(card);
  }

  // 日志
  const logBox=$('detail-events');logBox.replaceChildren();
  const events=(row.tasks||[]).flatMap(t=>(t.events||[]).map(e=>({at:e.at,text:numberLabel(t.serial)+'  '+e.message})))
                 .sort((a,b)=>a.at-b.at).slice(-300);
  for(const e of events){
    const d=document.createElement('div');d.className='exec-detail-event';
    const tm=document.createElement('span');tm.className='exec-detail-time';
    tm.textContent=new Date(e.at*1000).toLocaleTimeString();
    const tx=document.createElement('span');tx.textContent=e.text;
    d.append(tm,tx);logBox.append(d);
  }
  logBox.scrollTop=logBox.scrollHeight;
}
function prefillReply(t,row){
  const lead=t.lead||{};
  const plat=t.script_id===XHS_LEAD_SKILL?'xiaohongshu':(t.script_id===BILI_LEAD_SKILL?'bilibili':'douyin');
  state.quickView='leadx';state.leadxPlatform=plat;quickRecordId=null;
  setMode('quick');
  if($('leadx-keywords'))$('leadx-keywords').value=(lead.keywords||[]).join('\n');
  if($('leadx-intent'))$('leadx-intent').value=(lead.intent_words||[]).join(' ');
  if($('leadx-dm')&&lead.dm_template)$('leadx-dm').value=lead.dm_template;
  renderLeadxPlatform();
  const st=$('leadx-status');
  if(st){st.textContent='已带入「'+row.name+'」的配置：勾选回复用手机后点「开始回复」';st.className='exec-copy-status';}
}
function newQuickTask(){
  quickRecordId=null;state.tasks=[];stopPolling();clearTaskAreas();
  state.quickView='publish';
  state.selected.clear();state.assignments.clear();state.copies.clear();
  $('exec-title').value='';$('exec-tags').value='';$('exec-copy-result').value='';
  $('exec-asset').value='';$('exec-asset').onchange();
  renderPhones();syncObservation();renderAssignStatus();renderSide();
  setMode('quick');
  feedback('已新建任务，请选择手机、素材和文案','', 'quick');
}
function toggleQuick(id,on){
  const set=new Set(quickScripts);
  if(on)set.add(id);else set.delete(id);
  quickScripts=[...set];saveQuick();renderSide();
  ok(on?'已加入快捷入口':'已移出快捷入口');
}
function saveQuick(){try{localStorage.setItem('ff_quick_scripts',JSON.stringify(quickScripts.slice(-30)));}catch(e){}}

/* ---------- 模式 ---------- */
function setMode(m){
  state.mode=m;chat.dataset.mode=m;
  document.querySelectorAll('#exec-modes button').forEach(b=>b.classList.toggle('on',b.dataset.mode===m));
  // 快捷入口下：发布视频 / 截流 / 回复 / 手机一键 / 执行记录详情，平台在各面板顶部再选。
  // 2026-11-06：加 replypane（回复从截流拆出来，独立面板）。
  const view=(m==='quick')?((state.quickView==='leadx')?'leadx'
    :((state.quickView==='detail')?'detail'
    :((state.quickView==='onekey')?'onekey'
    :((state.quickView==='replypane')?'replypane':'publish')))):m;
  document.querySelectorAll('.exec-pane').forEach(p=>{p.hidden=p.dataset.pane!==view;});
  // 顶部的「选择手机」是给发布用的；截流/详情面板不显示，避免串台。
  const picker=document.querySelector('.exec-picker');
  if(picker)picker.hidden=(m==='quick'&&view!=='publish');
  // 详情面板自带确认卡与日志，隐藏全局进度/计划区避免重复
  const gp=$('exec-progress'),pl=$('exec-plan');
  if(gp)gp.hidden=(view==='detail');
  if(pl)pl.style.display=(view==='detail')?'none':'';
  if(view!=='detail')stopDetailTimer();
  // 切到截流面板时同步平台 Tab 与两组手机（否则从详情返回还显示上一个平台）
  if(view==='leadx'){try{renderLeadxPlatform();}catch(e){}}
  const sub={publish:'选平台 · 选手机 · 选素材 · 一键发布',
             leadx:'选平台 · 关键词 · 采集评论',
             replypane:'选平台 · 选采集批次 · 只填草稿不发送',
             onekey:'选手机 · 预制动作 · 批量执行',
             detail:'这条任务的进度、日志与操作',
             control:'用一句话描述任务',write:'和 AI 对话，把脚本写出来',video:'话题与参考图 → 提示词 → 生成'};
  $('exec-subtitle').textContent=sub[m]||'';
  // 每个模式有自己的左侧栏与会话；切换时恢复该模式最后使用的会话。
  state.sessionId=null;
  if(m==='control'||m==='write'){
    const list=sessions(m);
    if(!list.length)newSession(m);
    else switchSession(m,list[list.length-1].id);
  }
  renderSide();
  if(m==='quick'){
    refreshQuickHistory();
    if(state.quickView==='lead'&&state.tasks.length)renderLeadResults();
    if(state.quickView==='xhslead'&&state.tasks.length)renderXhsLeadResults();
  }
  // 每个界面显示自己的提示，不把上一个界面的结果带过来。
  paintFeedback();
  // 任务进度只属于发起它的界面；切走就收起，切回再刷新。
  if(state.mode===state.taskMode&&state.tasks.length)poll();else clearTaskAreas();
  const results=$('exec-results');
  if(results)results.hidden=m!=='video'||!results.children.length;
  if(m==='video'){
    videoStatus().catch(e=>log(e.message));
    if(videoProject){renderVideo();watchVideo();}
    else if(!state.newTask)loadLatestProject().catch(()=>{});
  }else{
    clearTimeout(state.videoTimer);
  }
}
async function loadLatestProject(){
  const v=await api('/studio/api/studio/projects');
  const latest=(v.projects||[])[0];
  if(!latest||!latest.count){renderSide();return;}
  videoProject=await api('/studio/api/studio/projects/'+latest.id);
  fillProjectForm(videoProject);
  renderVideo();
  if(videoProject.prompts.some(i=>['queued','running','submitted','downloading','recovering'].includes(i.status)))watchVideo();
}
async function loadRecords(box){
  try{
    const v=await api('/studio/api/studio/projects');
    box.replaceChildren();
    for(const p of (v.projects||[])){
      const row=document.createElement('div');row.className='exec-record-row';
      const open=document.createElement('button');open.type='button';
      open.className='exec-record'+((videoProject&&videoProject.id===p.id)?' active':'');
      const t=document.createElement('strong');
      t.textContent=(p.first_prompt||p.topic||'未命名').slice(0,42);
      const s=document.createElement('small');
      const when=new Date(p.created_at*1000).toLocaleTimeString();
      const state=p.prompt_count?p.ready+'/'+p.count+' 完成':'未生成提示词';
      s.textContent=when+' · '+state;
      open.append(t,s);
      open.onclick=()=>openProject(p.id);
      const del=document.createElement('button');
      del.type='button';del.className='exec-session-del';del.textContent='✕';
      del.title='删除这条生成记录（项目内视频文件一起删除）';
      del.onclick=async(e)=>{
        e.stopPropagation();
        if(!window.confirm('删除这条生成记录？项目里的视频文件会一起删掉，下载文件夹里的副本不受影响。'))return;
        try{
          const r=await fetch('/studio/api/studio/projects/'+p.id,{method:'DELETE'});
          if(!r.ok){const v2=await r.json().catch(()=>({}));throw Error(v2.detail||'删除失败');}
          if(videoProject&&videoProject.id===p.id)newVideoProject();
          else renderSide();
          ok('已删除该生成记录');
        }catch(err){feedback('删除失败：'+err.message,'err');}
      };
      row.append(open,del);
      box.append(row);
    }
    if(!(v.projects||[]).length){
      const tip=document.createElement('div');tip.className='exec-side-note';tip.textContent='还没有生成记录';box.append(tip);
    }
  }catch(e){box.textContent='读取失败：'+e.message;}
}
async function openProject(id){
  try{
    videoProject=await api('/studio/api/studio/projects/'+id);
    state.newTask=false;
    fillProjectForm(videoProject);
    renderVideo();renderSide();watchVideo();
    ok('已载入生成记录');
  }catch(e){feedback('载入失败：'+e.message,'err');}
}
function newVideoProject(){
  clearTimeout(state.videoTimer);
  videoProject=null;
  state.newTask=true;
  refFiles=[];
  renderThumbs();
  const list=$('video-list');if(list)list.replaceChildren();
  const res=$('exec-results');if(res){res.replaceChildren();res.hidden=true;}
  const gen=$('video-generate');if(gen)gen.hidden=true;
  $('video-topic').value='';
  $('video-taskid').value='';
  $('video-ref').value='';
  renderSide();
  feedback('已新建任务：填写话题后点“生成提示词”');
  $('video-topic').focus();
}
function fillProjectForm(p){  if(!p)return;
  if(p.topic)$('video-topic').value=p.topic;
  if(p.count)$('video-count').value=p.count;
  if(p.duration)$('video-duration').value=p.duration;
  if(p.resolution)$('video-quality').value=p.resolution;
  if(p.aspect)$('video-aspect').value=p.aspect;
  if(p.output_dir)$('video-output').value=p.output_dir;
}

/* ---------- 设备 ---------- */
async function refresh(){
  const d=await api('/api/devices');
  state.devices=(d.devices||[]).slice().sort((a,b)=>a.index-b.index);
  const a=await api('/api/actions');
  state.scripts=(a.scripts||[]).slice();
  renderPhones();syncObservation();renderSide();
}
function renderPhones(){
  const grid=$('exec-phone-grid');grid.replaceChildren();
  for(const d of state.devices){
    const b=document.createElement('button');
    b.type='button';b.className='exec-phone-number'+(state.selected.has(d.serial)?' on':'');
    b.textContent=String(d.index);
    b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial+' · '+d.state;
    b.setAttribute('aria-pressed',String(state.selected.has(d.serial)));
    b.disabled=d.state!=='online';
    b.onclick=()=>{
      if(state.selected.has(d.serial))state.selected.delete(d.serial);else state.selected.add(d.serial);
      renderPhones();syncObservation();renderAssignStatus();
    };
    grid.append(b);
  }
  $('exec-selection-count').textContent='已选 '+state.selected.size+' 台';
}
function syncObservation(){
  const sel=$('exec-device');if(!sel)return;
  const old=sel.value;
  // 观察手机不再依赖"先勾选"：直接把全部设备列进下拉（2026-11-03 调整）。
  sel.replaceChildren();option(sel,'','选择手机');
  for(const d of state.devices){
    option(sel,d.serial,d.index+'号 · '+(d.name||d.model||String(d.serial).slice(-6))+(d.state==='online'?'':'（离线）'));
  }
  if(state.devices.some(d=>d.serial===old))sel.value=old;
}

/* ---------- 任务轮询 ---------- */
function stopPolling(){if(state.timer){clearTimeout(state.timer);state.timer=null;}}
function clearTaskAreas(){
  const p=$('exec-progress');if(p){p.textContent='';p.hidden=true;}
  const plan=$('exec-plan');if(plan)plan.replaceChildren();
}
function poll(){
  stopPolling();
  if(!state.tasks.length){clearTaskAreas();return;}
  const owner=state.taskMode;
  if(state.mode!==owner){clearTaskAreas();}
  const ids=state.tasks.slice();
  Promise.all(ids.map(id=>api('/api/execution/tasks/'+id).catch(e=>({id,status:'unavailable',events:[{at:Date.now()/1000,message:'任务不可用：'+e.message}]}))))
  .then(rows=>{
    if(owner!==state.taskMode||ids.length!==state.tasks.length||ids.some((id,i)=>id!==state.tasks[i]))return;
    const visible=state.mode===owner;
    state.pending.clear();
    const plan=$('exec-plan');plan.replaceChildren();
    const lines=[];let active=false;
    for(const t of rows){
      if(!FINISHED.includes(t.status)&&t.status!=='unavailable')active=true;
      lines.push(numberLabel(t.serial)+' · '+(STATE_NAMES[t.status]||t.status));
      if(t.pending&&t.status==='awaiting_approval'){
        state.pending.set(t.id,t.pending.id);
        const card=document.createElement('div');card.className='exec-pending';
        const text=document.createElement('span');text.textContent=numberLabel(t.serial)+'：'+(t.pending.reason||'需要确认');
        const btn=document.createElement('button');btn.type='button';btn.className='exec-primary';btn.textContent='确认';
        btn.onclick=async()=>{
          btn.disabled=true;
          try{
            const latest=await api('/api/execution/tasks/'+t.id);
            if(!latest.pending||latest.pending.id!==t.pending.id)throw Error('本步已变化，请等待刷新');
            await api('/api/execution/tasks/'+t.id+'/approve',{id:latest.pending.id});
            ok(numberLabel(t.serial)+' 已确认');
          }catch(e){feedback('确认失败：'+e.message,'err');}
          finally{btn.disabled=false;poll();}
        };
        const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消任务';
        cancel.onclick=async()=>{
          cancel.disabled=true;btn.disabled=true;
          try{
            const result=await api('/api/execution/tasks/'+t.id+'/stop',{});
            feedback(numberLabel(t.serial)+' 已请求取消；'+(result.status==='stopping'?'正在等待在途操作结束':'任务已停止')+'，已发出的动作不会撤回','',state.taskMode);
          }catch(e){feedback(numberLabel(t.serial)+' 取消失败：'+e.message,'err',state.taskMode);cancel.disabled=false;btn.disabled=false;}
          finally{poll();}
        };
        card.append(text,btn,cancel);
        if(visible)plan.append(card);
      }
      if(t.status==='failed'||t.status==='needs_review'){
        const bar=document.createElement('div');bar.className='exec-task-bar';
        const reason=document.createElement('span');
        const events=t.events||[];
        const last=[...events].reverse().find(e=>/失败|中断|异常|未通过|不可用|未确认/.test(e.message||''));
        reason.textContent=numberLabel(t.serial)+'：'+(t.failure_reason||(last&&last.message)||'历史记录未保存具体原因，请检查设备状态后重新执行');
        bar.append(reason);
        const del=document.createElement('button');del.type='button';del.textContent='删除记录';
        del.onclick=async()=>{
          try{await fetch('/api/execution/tasks/'+t.id,{method:'DELETE'});state.tasks=state.tasks.filter(x=>x!==t.id);log('已删除任务记录');poll();}
          catch(e){log('删除失败：'+e.message);}
        };
        bar.append(del);if(visible)plan.append(bar);
      }
    }
    const progress=$('exec-progress');
    if(progress){progress.textContent=visible?lines.join(' · '):'';progress.hidden=!visible;}
    // 事件留一份在内存里供排查；界面不再有日志区，不能往已删除的节点写。
    state.lastEvents=rows.flatMap(t=>(t.events||[]).map(e=>({at:e.at,text:numberLabel(t.serial)+' '+e.message})))
                          .sort((a,b)=>a.at-b.at);
    if(rows.some(t=>t.quick_entry_id))refreshQuickHistory();
    // 截流视图：任务有产出就刷新线索卡（只读，不触发任何发送）。
    if(state.mode==='quick'&&state.quickView==='lead')renderLeadResults();
    if(state.mode==='quick'&&state.quickView==='xhslead')renderXhsLeadResults();
    if(active)state.timer=setTimeout(poll,1200);
    else feedback(rows.every(t=>t.status==='completed')?'全部完成':'已结束，请核验结果',
                  rows.every(t=>t.status==='completed')?'ok':'',state.taskMode);
  })
  .catch(e=>{log('轮询失败：'+e.message);state.timer=setTimeout(poll,2500);});
}

async function startTasks(plan,goal,scriptId,quickEntry=null,extra=null){
  if(!plan.length)throw Error('请先选择手机');
  const owner=state.mode;
  const batch=quickEntry?(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random()):'';
  const created=[];
  const failures=[];
  for(const p of plan){
    const body={serial:p.serial,goal:goal,package:'',script_id:scriptId||''};
    if(extra)Object.assign(body,extra);
    if(quickEntry){body.quick_entry_id=quickEntry.id;body.quick_entry_name=quickEntry.name;body.quick_batch_id=batch;}
    if(p.asset_id)body.asset_id=p.asset_id;
    if(p.title)body.title=p.title;
    if(p.caption)body.caption=p.caption;
    if(p.tags&&p.tags.length)body.tags=p.tags;
    try{const t=await api('/api/execution/tasks',body);created.push(t.id);}
    catch(e){failures.push(numberLabel(p.serial)+'：'+e.message);}
  }
  if(!created.length)throw Error('任务创建失败：'+failures.join('；'));
  state.taskMode=owner;
  state.tasks=created;
  if(quickEntry){quickRecordId=batch;refreshQuickHistory();}
  // 任务记入当前会话，切回来仍能看到。
  if(owner==='control'){
    const list=sessions(owner);
    const i=list.findIndex(s=>s.id===state.sessionId);
    if(i>=0){list[i].taskIds=(list[i].taskIds||[]).concat(state.tasks);storeSessions(owner,list);}
    message('assistant','已在 '+state.tasks.length+' 台手机下发');
  }
  if(failures.length)feedback('已下发 '+state.tasks.length+' 台；未创建：'+failures.join('；'),'err',owner);
  else ok('已下发 '+state.tasks.length+' 台，等待执行',owner);
  poll();
}

/* ---------- 一键发布 ---------- */
function buildPlan(){
  const asset=$('exec-asset').value;
  const title=$('exec-title').value.trim();
  const caption=$('exec-copy-result').value.trim();
  const tags=$('exec-tags').value.split(/\s+/).filter(Boolean).slice(0,5);
  return [...state.selected].map(serial=>{
    const aid=state.assignments.get(serial)||asset;
    const c=(aid&&aid!==asset)?state.copies.get(aid):null;
    return {serial,asset_id:aid,title:c?c.title:title,caption:c?c.description:caption,tags:(c?c.tags:tags).slice(0,5)};
  });
}
async function runQuick(){
  if(!state.selected.size)throw Error('请先选择手机');
  const plan=buildPlan();
  const bad=plan.filter(p=>!p.asset_id||!p.title||!p.caption);
  if(bad.length)throw Error('缺少素材或文案：'+bad.map(p=>numberLabel(p.serial)).join('、'));
  if(plan.some(p=>p.title.length>20))throw Error('标题超过 20 字');
  await startTasks(plan,'执行抖音视频发布',QUICK_SKILL,{id:QUICK_SKILL,name:'抖音视频发布'});
}
/* 小红书发布：复用同一套素材与文案，但按小红书规则校验。
   差异：标题 20 字硬上限、正文上限远高于抖音、话题可到 10 个、标题与正文允许只填一个。 */
async function runQuickXhs(){
  if(!state.selected.size)throw Error('请先选择手机');
  const plan=buildPlan().map(p=>({...p,tags:(p.tags||[]).slice(0,XHS_TAG_LIMIT)}));
  const bad=plan.filter(p=>!p.asset_id);
  if(bad.length)throw Error('请先选择素材：'+bad.map(p=>numberLabel(p.serial)).join('、'));
  const empty=plan.filter(p=>!p.title&&!p.caption);
  if(empty.length)throw Error('标题和正文不能同时为空：'+empty.map(p=>numberLabel(p.serial)).join('、'));
  const longTitle=plan.filter(p=>p.title.length>XHS_TITLE_LIMIT);
  if(longTitle.length)throw Error('小红书标题最多 '+XHS_TITLE_LIMIT+' 字：'+longTitle.map(p=>numberLabel(p.serial)).join('、'));
  const longBody=plan.filter(p=>p.caption.length>XHS_BODY_LIMIT);
  if(longBody.length)throw Error('小红书正文最多 '+XHS_BODY_LIMIT+' 字：'+longBody.map(p=>numberLabel(p.serial)).join('、'));
  await startTasks(plan,'执行小红书笔记发布',XHS_SKILL,{id:XHS_SKILL,name:'小红书发布'});
}

/* ---------- 抖音截流 ---------- */
function openLeadPanel(){
  state.quickView='lead';
  quickRecordId=null;
  setMode('quick');
  const box=$('lead-results');
  if(box&&!box.children.length)box.append(leadHint('还没有线索。填好关键词和意向词后点“开始采集”。'));
  // 渲染「采集用 / 回复用」两套设备选择器（各自独立，不跟全局选中走）
  renderLeadDevices();
  paintFeedback();
}
function leadHint(text){
  const tip=document.createElement('div');tip.className='exec-side-note';tip.textContent=text;return tip;
}
function splitWords(raw){
  return String(raw||'').split(/[\s,，、;；]+/).map(s=>s.trim()).filter(Boolean);
}
function leadConfig(){
  const keywords=String($('lead-keywords').value||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean).slice(0,20);
  const intent=splitWords($('lead-intent').value).slice(0,50);
  const exclude=splitWords($('lead-exclude').value).slice(0,50);
  const reply=$('lead-reply').value.trim();
  const dm=$('lead-dm').value.trim();
  if(!keywords.length)throw Error('请至少填一个关键词');
  // 意向词留空 = 不筛意向、全量收录（面板标签和后端都是这个约定）。
  // 旧代码在这里强制要求意向词，跟标签"可选"矛盾，2026-11-02 实测被卡住过。
  if(!reply&&!dm)throw Error('自动回复内容和私信内容至少填一项');
  return {
    keywords:keywords,
    sort:$('lead-sort').value,
    publish_time:$('lead-publish-time').value,
    duration:$('lead-duration').value,
    scope:$('lead-scope').value,
    distance:$('lead-distance').value,
    videos_per_keyword:Math.max(1,Math.min(50,parseInt($('lead-videos').value||'5',10)||5)),
    comments_per_video:Math.max(1,Math.min(200,parseInt($('lead-comments').value||'20',10)||20)),
    intent_words:intent,
    exclude_words:exclude,
    reply_template:reply,
    dm_template:dm,
    cooldown_every:Math.max(1,Math.min(500,parseInt($('lead-cd-every').value||'10',10)||10)),
    cooldown_seconds:Math.max(1,Math.min(3600,parseInt($('lead-cd-seconds').value||'60',10)||60))
  };
}
/* ---------- 抖音截流：设备双选择器（采集/回复各自独立） ---------- */
const dyCollect=new Set();
const dyReply=new Set();
function renderLeadDevices(){
  const paint=(box,set)=>{
    if(!box)return;
    box.replaceChildren();
    for(const d of state.devices){
      const b=document.createElement('button');
      b.type='button';b.className='exec-phone-number'+(set.has(d.serial)?' on':'');
      b.textContent=String(d.index);
      b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
      b.disabled=d.state!=='online';
      b.onclick=()=>{ if(set.has(d.serial))set.delete(d.serial);else set.add(d.serial); renderLeadDevices(); };
      box.append(b);
    }
  };
  paint($('lead-collect-devices'),dyCollect);
  paint($('lead-reply-devices'),dyReply);
}
let leadReplyRunning=false;
async function runLeadReply(){
  if(!dyReply.size)throw Error('请先勾选「回复用手机」');
  // 条数上限：默认 20，避免一次跑太久；可在参数里改
  const limitEl=$('lead-reply-limit');
  const limit=Math.max(1,Math.min(200,parseInt(limitEl&&limitEl.value||'20',10)||20));
  const intervalEl=$('lead-interval');
  const interval=Math.max(0,Math.min(600,parseInt(intervalEl&&intervalEl.value||'0',10)||0));
  const serials=[...dyReply];
  const dmText=($('lead-dm').value||'').trim();
  leadReplyRunning=true;
  let done=0,okCount=0,failCount=0,round=0;
  const st=$('lead-status');
  st.textContent='开始回复：0/'+limit;
  try{
    while(leadReplyRunning && done<limit){
      const serial=serials[round % serials.length];round++;
      try{
        const r=await api('/api/execution/leads/extract',{
          confirm:true,platform:'douyin',serial:serial,
          entry:'search_account',dm_text:dmText
        });
        if(!r||!r.claimed){ st.textContent='池里没有可回复的线索了（已处理 '+done+' 条）'; break; }
        done++;
        if(r.result==='found')okCount++;else failCount++;
        st.textContent='回复中：'+done+'/'+limit+'　成功 '+okCount+'　失败 '+failCount;
      }catch(e){
        done++;failCount++;
        st.textContent='回复中：'+done+'/'+limit+'　出错：'+String(e.message||'').slice(0,40);
      }
      if(leadReplyRunning && interval>0 && done<limit)await new Promise(r=>setTimeout(r,interval*1000));
    }
  }finally{
    leadReplyRunning=false;
    st.textContent='回复结束：成功 '+okCount+' / 共处理 '+done+'（失败 '+failCount+'）';
    // 这里不刷新结果列表：回复走的是 /leads/extract 单条接口，没有任务 ID，
    // renderLeadResults() 依赖 state.tasks 渲染不出来。
    // 想看完整结果请到「线索数据」面板，那里按 /leads/pool 分页拉全量。
  }
}
async function runLead(){
  // 采集用「采集用手机」那套选择，不复用全局选中：
  // 全局选中是给发布等任务用的，跟着它走会串台（B站面板踩过这个坑）。
  if(!dyCollect.size)throw Error('请先勾选「采集用手机」');
  const cfg=leadConfig();
  cfg.collect_serials=[...dyCollect];
  $('lead-status').textContent='正在下发…';
  const plan=[...dyCollect].map(serial=>({serial}));
  try{
    await startTasks(plan,'抖音截流：'+cfg.keywords.slice(0,3).join('、'),LEAD_SKILL,{id:LEAD_SKILL,name:'抖音截流'},{lead:cfg});
    $('lead-status').textContent='已下发，采集中';
    const box=$('lead-results');if(box)box.replaceChildren(leadHint('采集中：会自动搜索、看评论；结束后这里会列出线索。'));
  }finally{
    if($('lead-status').textContent==='正在下发…')$('lead-status').textContent='';
  }
}
async function stopLead(){
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的截流任务','err','quick');return;}
  for(const id of ids){
    try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}
  }
  $('lead-status').textContent='已请求停止';
  ok('已请求停止；已发出的动作不能撤回','quick');
}
/* ---------- 小红书截流（A 采集 / B 回复） ---------- */
function openXhsLeadPanel(){
  state.quickView='xhslead';
  quickRecordId=null;
  setMode('quick');
  const box=$('xhs-results');
  if(box&&!box.children.length)box.append(leadHint('还没有线索。填好关键词和意向词后点“开始采集”。'));
  if($('xhs-dm-save'))$('xhs-dm-save').onclick=()=>saveXhsPhrase().catch(e=>log(e.message));
  if($('xhs-dm-del'))$('xhs-dm-del').onclick=()=>delXhsPhrase().catch(e=>log(e.message));
  if($('xhs-dm-phrase'))$('xhs-dm-phrase').onchange=()=>{
    const id=$('xhs-dm-phrase').value;
    if(id&&_xhsPhraseCache[id]!==undefined)$('xhs-dm').value=_xhsPhraseCache[id];
  };
  loadXhsPhrases();
  renderXhsDevices();
  if($('xhs-reply'))$('xhs-reply').onclick=async()=>{
    if(state.busy)return;state.busy=true;
    try{await runXhsReply();}catch(e){feedback(e.message,'err','quick');$('xhs-status').textContent='';}finally{state.busy=false;}
  };
  paintFeedback();
}
const xhsCollect=new Set();
const xhsReply=new Set();
function renderXhsDevices(){
  const paint=(box,set)=>{
    if(!box)return;
    box.replaceChildren();
    for(const d of state.devices){
      const b=document.createElement('button');
      b.type='button';b.className='exec-phone-number'+(set.has(d.serial)?' on':'');
      b.textContent=String(d.index);
      b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
      b.disabled=d.state!=='online';
      b.onclick=()=>{ if(set.has(d.serial))set.delete(d.serial);else set.add(d.serial); renderXhsDevices(); };
      box.append(b);
    }
  };
  paint($('xhs-collect-devices'),xhsCollect);
  paint($('xhs-reply-devices'),xhsReply);
}
const _xhsPhraseCache={};
async function loadXhsPhrases(){
  const sel=$('xhs-dm-phrase');if(!sel)return;
  const keep=sel.value;
  sel.length=1;
  try{
    const r=await api('/api/phrases');
    (r.phrases||[]).forEach(p=>{
      _xhsPhraseCache[p.id]=p.text;
      const o=document.createElement('option');o.value=p.id;o.textContent=(p.name||p.text||'').slice(0,30);
      sel.append(o);
    });
    if(keep)sel.value=keep;
  }catch(e){/* 常用语拉取失败不影响主流程 */}
}
async function saveXhsPhrase(){
  const t=$('xhs-dm').value.trim();
  if(!t)throw Error('私信内容为空，无法存为常用语');
  const r=await api('/api/phrases',{text:t});
  await loadXhsPhrases();
  $('xhs-dm-phrase').value=r.id||'';
  ok('已存为常用语','quick');
}
async function delXhsPhrase(){
  const sel=$('xhs-dm-phrase');
  const id=sel?sel.value:'';
  if(!id)throw Error('请先在下拉里选中要删除的常用语');
  await apiDelete('/api/phrases/'+encodeURIComponent(id));
  await loadXhsPhrases();
  ok('已删除','quick');
}
async function runXhsReply(){
  if(!xhsReply.size)throw Error('请先勾选「回复用手机」');
  const cfg=xhsLeadConfig();
  const limit=Math.max(1,Math.min(200,parseInt($('xhs-reply-limit').value||'50',10)||50));
  const interval=Math.max(0,Math.min(600,parseInt($('xhs-reply-interval').value||'8',10)||0));
  const cdEvery=Math.max(1,Math.min(200,parseInt($('xhs-reply-cd-every').value||'10',10)||10));
  const cdSeconds=Math.max(0,Math.min(3600,parseInt($('xhs-reply-cd-seconds').value||'60',10)||0));
  $('xhs-status').textContent='正在下发回复任务…';
  const plan=[...xhsReply].map(serial=>({serial}));
  try{
    await startTasks(plan,'小红书回复（填草稿）：'+limit+' 条',
                     XHS_DM_SKILL,{id:XHS_DM_SKILL,name:'小红书私信'},
                     {lead:{dm_template:cfg.dm_template,reply_limit:limit,
                            interval_seconds:interval,cooldown_every:cdEvery,
                            cooldown_seconds:cdSeconds}});
    $('xhs-status').textContent='已下发，正在填草稿（不会发送）';
  }finally{
    if($('xhs-status').textContent==='正在下发回复任务…')$('xhs-status').textContent='';
  }
}
function xhsLeadConfig(){
  const keywords=String($('xhs-keywords').value||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean).slice(0,20);
  const intent=splitWords($('xhs-intent').value).slice(0,50);
  const exclude=splitWords($('xhs-exclude').value).slice(0,50);
  const dm=$('xhs-dm').value.trim();
  if(!keywords.length)throw Error('请至少填一个关键词');
  if(!intent.length)throw Error('请至少填一个意向关键词，否则无法判断谁值得跟进');
  if(!dm)throw Error('请填写私信内容，采集到的线索需要私信草稿');
  const num=(el,lo,hi,def)=>{const v=parseInt($(el).value||String(def),10);return Number.isFinite(v)?Math.max(lo,Math.min(hi,v)):def;};
  return {
    keywords:keywords,

    videos_per_keyword:num('xhs-videos',1,50,5),
    comments_per_video:num('xhs-comments',1,200,20),
    comment_screens:num('xhs-screens',1,40,20),
    intent_words:intent,
    exclude_words:exclude,
    dm_template:dm,
    cooldown_every:num('xhs-cd-every',1,500,10),
    cooldown_seconds:num('xhs-cd-seconds',1,3600,60)
  };
}
async function runXhsLead(){
  if(!xhsCollect.size)throw Error('请先勾选「采集用手机」');
  const cfg=xhsLeadConfig();
  $('xhs-status').textContent='正在下发…';
  const plan=[...xhsCollect].map(serial=>({serial}));
  try{
    await startTasks(plan,'小红书截流：'+cfg.keywords.slice(0,3).join('、'),
                     XHS_LEAD_SKILL,{id:XHS_LEAD_SKILL,name:'小红书截流'},{lead:cfg});
    $('xhs-status').textContent='已下发，采集中';
    const box=$('xhs-results');
    if(box)box.replaceChildren(leadHint('采集中：会自动搜索、进笔记读评论、点头像取小红书号；结束后这里列出线索。'));
  }finally{
    if($('xhs-status').textContent==='正在下发…')$('xhs-status').textContent='';
  }
}
/* ---------- B站截流（采集评论者UID → 中转站 → 换号私信） ---------- */
function openBiliLeadPanel(){
  state.quickView='bilead';
  quickRecordId=null;
  setMode('quick');
  const box=$('bl-results');
  if(box&&!box.children.length)box.append(leadHint('还没有线索。填好关键词后点「开始采集」。'));
  // 常用语：绑定按钮 + 首次打开时拉一次列表
  if($('bl-dm-save'))$('bl-dm-save').onclick=()=>saveBiliPhrase().catch(e=>log(e.message));
  if($('bl-dm-del'))$('bl-dm-del').onclick=()=>delBiliPhrase().catch(e=>log(e.message));
  if($('bl-dm-phrase'))$('bl-dm-phrase').onchange=()=>{
    const id=$('bl-dm-phrase').value;
    if(id&&_biliPhraseCache[id]!==undefined)$('bl-dm').value=_biliPhraseCache[id];
  };
  loadBiliPhrases();
  renderBiliDevices();
  if($('bl-reply'))$('bl-reply').onclick=()=>runBiliReply().catch(e=>{$('bl-status').textContent='';log(e.message);});
  paintFeedback();
}
const blCollect=new Set();
const blReply=new Set();
function renderBiliDevices(){
  const paint=(box,set)=>{
    if(!box)return;
    box.replaceChildren();
    for(const d of state.devices){
      const b=document.createElement('button');
      b.type='button';b.className='exec-phone-number'+(set.has(d.serial)?' on':'');
      b.textContent=String(d.index);
      b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
      b.disabled=d.state!=='online';
      b.onclick=()=>{ if(set.has(d.serial))set.delete(d.serial);else set.add(d.serial); renderBiliDevices(); };
      box.append(b);
    }
  };
  paint($('bl-collect-devices'),blCollect);
  paint($('bl-reply-devices'),blReply);
}
async function runBiliReply(){
  if(!blReply.size)throw Error('请先勾选「回复用手机」');
  const cfg=biliLeadConfig();
  const limit=Math.max(1,Math.min(200,parseInt($('bl-reply-limit').value||'50',10)||50));
  $('bl-status').textContent='正在下发回复任务…';
  const plan=[...blReply].map(serial=>({serial}));
  try{
    const clampI=(el,lo,hi,def)=>{const v=parseInt($(el).value||String(def),10);return Number.isFinite(v)?Math.max(lo,Math.min(hi,v)):def;};
    const interval=clampI('bl-interval',0,600,5);
    const coolEvery=clampI('bl-cool-every',1,500,10);
    const coolSec=clampI('bl-cool-sec',0,3600,60);
    await startTasks(plan,'B站回复（填草稿）：'+String(limit)+' 条',
                     'builtin:bilibili-dm',{id:'builtin:bilibili-dm',name:'B站私信'},
                     {lead:{dm_template:cfg.dm_template,reply_limit:limit,
                            interval_seconds:interval,
                            cooldown_every:coolEvery,
                            cooldown_seconds:coolSec}});
    $('bl-status').textContent='已下发，正在填草稿';
  }finally{
    if($('bl-status').textContent==='正在下发回复任务…')$('bl-status').textContent='';
  }
}
const _biliPhraseCache={};
async function loadBiliPhrases(){
  const sel=$('bl-dm-phrase');if(!sel)return;
  const keep=sel.value;
  sel.length=1;
  try{
    const r=await api('/api/phrases');
    (r.phrases||[]).forEach(p=>{
      const label=(p.text||'').replace(/\s+/g,' ').slice(0,36);
      const o=option(sel,p.id,label||'(空)');
      o.dataset.text=p.text||'';
      _biliPhraseCache[p.id]=p.text||'';
    });
    if(keep&&[...sel.options].some(o=>o.value===keep))sel.value=keep;
  }catch(e){log('常用语加载失败：'+e.message);}
}
async function saveBiliPhrase(){
  const t=$('bl-dm').value.trim();
  if(!t)throw Error('私信内容为空，无法存为常用语');
  const r=await api('/api/phrases',{text:t});
  await loadBiliPhrases();
  $('bl-dm-phrase').value=r.id||'';
  log('已存为常用语');
}
async function delBiliPhrase(){
  const sel=$('bl-dm-phrase');
  const id=sel?sel.value:'';
  if(!id)throw Error('请先在下拉里选中要删除的常用语');
  await apiDelete('/api/phrases/'+encodeURIComponent(id));
  await loadBiliPhrases();
  log('已删除常用语');
}
function biliLeadConfig(){
  const keywords=String($('bl-keywords').value||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean).slice(0,20);
  const dm=$('bl-dm').value.trim();
  if(!keywords.length)throw Error('请至少填一个关键词');
  const num=(el,lo,hi,def)=>{const v=parseInt($(el).value||String(def),10);return Number.isFinite(v)?Math.max(lo,Math.min(hi,v)):def;};
  return {
    keywords:keywords,
    videos_per_keyword:num('bl-videos',1,50,5),
    comments_per_video:num('bl-comments',1,30,10),
    sort_by:($('bl-sort').value||'默认排序'),
    sort:($('bl-sort').value||'默认排序'),
    dm_template:dm
  };
}
async function runBiliLead(){
  if(!blCollect.size)throw Error('请先勾选「采集用手机」');
  const cfg=biliLeadConfig();
  $('bl-status').textContent='正在下发…';
  const plan=[...blCollect].map(serial=>({serial}));
  try{
    await startTasks(plan,'B站截流：'+cfg.keywords.slice(0,3).join('、'),
                     BILI_LEAD_SKILL,{id:BILI_LEAD_SKILL,name:'B站截流'},{lead:cfg});
    $('bl-status').textContent='已下发，采集中';
    const box=$('bl-results');
    if(box)box.replaceChildren(leadHint('采集中：自动搜索 → 进视频读评论 → 点评论者昵称取 UID；结束后线索写入中转站（待处理状态）。'));
  }finally{
    if($('bl-status').textContent==='正在下发…')$('bl-status').textContent='';
  }
}
async function stopBiliLead(){
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的任务','err','quick');return;}
  for(const id of ids){
    try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}
  }
  $('bl-status').textContent='已请求停止';
}

async function stopXhsLead(){
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的任务','err','quick');return;}
  for(const id of ids){
    try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}
  }
  $('xhs-status').textContent='已请求停止';
  ok('已请求停止；已发出的动作不能撤回','quick');
}
function xhsLeadStatsText(s){
  const parts=[];
  if(s.keywords)parts.push('关键词 '+s.keywords);
  if(s.notes)parts.push('笔记 '+s.notes);
  if(s.comments)parts.push('评论 '+s.comments);
  if(s.leads)parts.push('线索 '+s.leads);
  if(typeof s.ids==='number')parts.push('小红书号 '+s.ids);
  return parts.join(' · ');
}
async function renderXhsLeadResults(){
  const ids=state.tasks.slice();
  if(!ids.length)return;
  const box=$('xhs-results');if(!box)return;
  const rows=[];
  for(const id of ids){
    try{
      const data=await api('/api/execution/leads/'+id);
      const stats=data.stats||{};
      // 只认小红书任务：它的 stats 里有 ids（取到小红书号的条数），抖音版没有这个字段。
      const isXhs=('ids' in stats)||(data.leads||[]).some(r=>'xhs_id' in r);
      if(!isXhs)continue;
      for(const item of (data.leads||[]))rows.push({...item,taskId:id});
      if(stats&&$('xhs-status'))$('xhs-status').textContent=xhsLeadStatsText(stats);
    }catch(e){/* 任务还没产出结果时忽略 */}
  }
  box.replaceChildren();
  if(!rows.length){
    box.append(leadHint('还没有命中线索。可以放宽意向关键词，或换更具体的关键词再跑一次。'));
    return;
  }
  const withId=rows.filter(r=>r.xhs_id).length;
  const head=document.createElement('div');head.className='exec-side-note';
  head.textContent='线索 '+rows.length+' 条，已取到小红书号 '+withId+' 条（只读采集，未发送任何内容）';
  box.append(head);
  for(const row of rows){
    const card=document.createElement('div');card.className='exec-lead-card';
    const who=document.createElement('strong');
    who.textContent=(row.author||'未知用户')+(row.region?' · '+row.region:'');
    card.append(who);
    // 小红书号：唯一键。B 端只能靠它定位，昵称重名不可用。
    const idLine=document.createElement('div');idLine.className='exec-lead-id';
    if(row.xhs_id){
      const tag=document.createElement('span');tag.textContent='小红书号 '+row.xhs_id;
      const copy=document.createElement('button');copy.type='button';copy.textContent='复制';
      copy.onclick=()=>{navigator.clipboard?.writeText(String(row.xhs_id));copy.textContent='已复制';};
      idLine.append(tag,copy);
    }else{
      idLine.textContent='未取到小红书号：该条无法用于 B 端定位';
    }
    card.append(idLine);
    if(row.note_title){
      const nt=document.createElement('small');nt.className='exec-lead-why';
      nt.textContent='笔记：'+row.note_title;card.append(nt);
    }
    const text=document.createElement('p');text.className='exec-lead-text';text.textContent=row.content||'';
    card.append(text);
    const why=document.createElement('small');why.className='exec-lead-why';
    why.textContent=(row.reason||'')+(row.keyword?'　关键词：'+row.keyword:'');
    card.append(why);
    for(const [label,value] of [['私信草稿（未发送）',row.dm_draft]]){
      if(!value)continue;
      const d=document.createElement('div');d.className='exec-lead-draft';
      const l=document.createElement('small');l.textContent=label;
      const p=document.createElement('p');p.textContent=value;
      d.append(l,p);card.append(d);
    }
    box.append(card);
  }
}

async function renderLeadResults(){
  const ids=state.tasks.slice();
  if(!ids.length)return;
  const box=$('lead-results');if(!box)return;
  const rows=[];
  for(const id of ids){
    try{
      const data=await api('/api/execution/leads/'+id);
      for(const item of (data.leads||[]))rows.push({...item,taskId:id});
      if(data.stats&&$('lead-status'))$('lead-status').textContent=leadStatsText(data.stats);
    }catch(e){/* 任务还没产出结果时忽略 */}
  }
  box.replaceChildren();
  if(!rows.length){
    box.append(leadHint('还没有命中线索。可以放宽意向关键词，或换更具体的关键词再跑一次。'));
    return;
  }
  const head=document.createElement('div');head.className='exec-side-note';
  head.textContent='线索 '+rows.length+' 条（只读采集，未发送任何内容）';
  box.append(head);
  for(const row of rows){
    const card=document.createElement('div');card.className='exec-lead-card';
    const who=document.createElement('strong');who.textContent=(row.author||'未知用户')+(row.region?' · '+row.region:'')+(row.age?' · '+row.age:'');
    const text=document.createElement('p');text.className='exec-lead-text';text.textContent=row.content||'';
    const why=document.createElement('small');why.className='exec-lead-why';why.textContent=(row.reason||'')+(row.keyword?'　关键词：'+row.keyword:'');
    card.append(who,text,why);
    if(row.reply_draft){
      const d=document.createElement('div');d.className='exec-lead-draft';
      const label=document.createElement('small');label.textContent='回复草稿（未发送）';
      const body=document.createElement('p');body.textContent=row.reply_draft;
      d.append(label,body);card.append(d);
    }
    if(row.dm_draft){
      const d=document.createElement('div');d.className='exec-lead-draft';
      const label=document.createElement('small');label.textContent='私信草稿（未发送）';
      const body=document.createElement('p');body.textContent=row.dm_draft;
      d.append(label,body);card.append(d);
    }
    box.append(card);
  }
}
function leadStatsText(stats){
  if(!stats)return '';
  return '已看 '+stats.videos+' 个视频、'+stats.comments+' 条评论，命中 '+stats.leads+' 条线索';
}


async function runTask(){
  if(!state.selected.size)throw Error('请先选择手机');
  const goal=$('exec-goal').value.trim();
  if(!goal)throw Error('请填写任务指令');
  const list=sessions('control');
  const i=list.findIndex(s=>s.id===state.sessionId);
  if(i>=0){list[i].goal=goal;storeSessions('control',list);}
  message('user',goal);
  await startTasks([...state.selected].map(serial=>({serial})),goal,'');
}
async function runScript(scriptId,quickEntry=null){
  if(!state.selected.size){feedback('请先选择手机','err');return;}
  const name=(state.scripts.find(s=>s.id===scriptId)||{}).name||'脚本';
  await startTasks([...state.selected].map(serial=>({serial})),'执行脚本：'+name,scriptId,quickEntry);
}

/* ---------- 脚本写作 ---------- */
function renderWrite(){
  const box=$('exec-write-log');box.replaceChildren();
  if(!state.writeHistory.length){
    const tip=document.createElement('div');tip.className='exec-side-note';tip.textContent='先输入想做的操作，AI 会给出结构化脚本，可继续用对话修改。';
    box.append(tip);return;
  }
  for(const m of state.writeHistory){
    const d=document.createElement('div');d.className='exec-bubble '+m.role;
    const who=document.createElement('strong');who.textContent=m.role==='user'?'我':'AI';
    const body=document.createElement('div');body.textContent=m.content;
    d.append(who,body);box.append(d);
  }
  box.scrollTop=box.scrollHeight;
}
function setDraft(draft){state.draft=draft;$('exec-draft').value=draft?JSON.stringify(draft,null,2):'';saveWriteSession();}
function saveWriteSession(){
  if(state.mode!=='write'||!state.sessionId)return;
  const list=sessions('write');
  const i=list.findIndex(s=>s.id===state.sessionId);
  if(i<0)return;
  list[i].draft=state.draft;
  list[i].history=state.writeHistory.slice(-40);
  if((list[i].title==='新写作'||!list[i].title)&&state.writeHistory.length)
    list[i].title=String(state.writeHistory[0].content||'').slice(0,16);
  storeSessions('write',list);
  renderSide();
}

$('exec-write-start').onclick=async()=>{
  try{
    const goal=$('exec-write-input').value.trim();
    if(!goal)throw Error('请填写想实现的操作');
    feedback('正在生成脚本草稿…');
    const d=await api('/api/execution/draft',{goal,package:''});
    state.writeHistory=[{role:'user',content:goal},{role:'assistant',content:'已生成草稿：'+(d.name||'')}];
    setDraft(d);renderWrite();ok('草稿已生成，未保存、未执行');
  }catch(e){feedback('生成失败：'+e.message,'err');}
};
$('exec-write-send').onclick=async()=>{
  try{
    if(!state.draft)throw Error('请先生成草稿');
    const message=$('exec-write-input').value.trim();
    if(!message)throw Error('请填写修改要求');
    feedback('正在按对话修改草稿…');
    const history=state.writeHistory.slice(-20).map(m=>({role:m.role,content:m.content}));
    const d=await api('/api/execution/draft/revise',{draft:state.draft,message,history});
    state.writeHistory.push({role:'user',content:message},{role:'assistant',content:'已按对话更新草稿'});
    setDraft(d);renderWrite();ok('草稿已更新，未保存、未执行');
  }catch(e){feedback('修改失败：'+e.message,'err');}
};
$('exec-write-save').onclick=async()=>{
  try{
    if(!state.draft)throw Error('请先生成草稿');
    const saved=await api('/api/execution/scripts',state.draft);
    state.savedId=saved.id;
    await refresh();
    ok('脚本已保存：'+(saved.name||saved.id)+'（保存不会执行）');
  }catch(e){feedback('保存失败：'+e.message,'err');}
};
$('exec-write-quick').onclick=async()=>{
  try{
    if(!state.savedId)throw Error('请先保存脚本，再加入快捷入口');
    toggleQuick(state.savedId,true);
  }catch(e){feedback('加入失败：'+e.message,'err');}
};
if($('exec-write-try'))$('exec-write-try').onclick=async()=>{
  try{
    if(!state.savedId)throw Error('请先保存脚本，再下发试跑');
    if(!state.selected.size)throw Error('请先在顶部勾选要试跑的手机（发布页顶部）');
    state.writeHistory.push({role:'user',content:'请求下发到 '+[...state.selected].map(numberLabel).join('、')+' 试跑'},{role:'assistant',content:'已下发试跑，请到执行记录查看日志，有问题把日志贴回来我继续改'});
    renderWrite();saveWriteSession();
    setMode('control');runScript(state.savedId,{id:state.savedId,name:'写作会话试跑'});
  }catch(e){feedback('试跑失败：'+e.message,'err');}
};

/* ---------- 视频生成 ---------- */
let videoProject=null;
let refFiles=[];
let dragFrom=null;
function moveReference(from,to){
  if(!Number.isInteger(from)||!Number.isInteger(to)||from<0||to<0||from>=refFiles.length||to>=refFiles.length||from===to)return;
  const [moved]=refFiles.splice(from,1);refFiles.splice(to,0,moved);
  renderThumbs();
}
function renderThumbs(){
  const box=$('video-thumbs');if(!box)return;
  box.replaceChildren();
  refFiles.forEach((file,index)=>{
    const cell=document.createElement('div');
    cell.className='exec-thumb';cell.draggable=true;cell.dataset.index=String(index);
    const img=document.createElement('img');img.alt='图片'+(index+1);img.draggable=false;
    const url=URL.createObjectURL(file);img.src=url;
    img.onload=img.onerror=()=>URL.revokeObjectURL(url);
    const tag=document.createElement('span');tag.textContent='图片'+(index+1);
    const remove=document.createElement('button');remove.type='button';remove.className='exec-thumb-remove';
    remove.textContent='×';remove.title='移除图片'+(index+1);remove.setAttribute('aria-label',remove.title);
    remove.addEventListener('click',()=>{refFiles.splice(index,1);renderThumbs();});
    cell.append(img,tag,remove);
    cell.addEventListener('dragstart',(e)=>{
      dragFrom=index;cell.classList.add('dragging');
      if(e.dataTransfer){e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',String(index));}
    });
    cell.addEventListener('dragend',()=>{cell.classList.remove('dragging');dragFrom=null;box.querySelectorAll('.over').forEach(x=>x.classList.remove('over'));});
    cell.addEventListener('dragover',(e)=>{e.preventDefault();if(e.dataTransfer)e.dataTransfer.dropEffect='move';cell.classList.add('over');});
    cell.addEventListener('dragleave',()=>cell.classList.remove('over'));
    cell.addEventListener('drop',(e)=>{
      e.preventDefault();e.stopPropagation();cell.classList.remove('over');
      const from=dragFrom;dragFrom=null;moveReference(from,index);
    });
    box.append(cell);
  });
  if(!refFiles.length){
    const tip=document.createElement('div');tip.className='exec-side-note';
    tip.textContent='未选择参考图，可直接生成；多张时按图片1、图片2…顺序参考';
    box.append(tip);
  }
}
$('video-ref').onchange=()=>{
  const files=[...($('video-ref').files||[])];
  const remaining=Math.max(0,9-refFiles.length);
  refFiles.push(...files.slice(0,remaining));
  $('video-ref').value='';
  if(files.length>remaining)feedback('参考图最多 9 张，本次超出的图片未添加','err','video');
  renderThumbs();
};
function videoStatus(){
  return api('/api/workspace/video/status').then(v=>{
    $('video-status').textContent=(v.enabled&&v.configured?'生成服务已配置':'生成服务未启用')+' · '+(v.cost||'');
    return v;
  });
}
$('video-doctor').onclick=async()=>{
  const st=$('video-status');
  st.textContent='正在检测视频服务…';st.className='exec-copy-status';
  try{
    const r=await api('/studio/api/studio/video/doctor');
    st.textContent=(r.ok?'服务可用':'服务不可用')+' · '+(r.detail||'').replace(/\s+/g,' ').slice(0,220);
    st.className='exec-copy-status '+(r.ok?'ok':'err');
  }catch(e){st.textContent='检测失败：'+e.message;st.className='exec-copy-status err';}
};
$('video-pull').onclick=async()=>{
  const st=$('video-status');
  try{
    if(!videoProject)throw Error('请先载入或新建一个生成记录');
    const taskId=$('video-taskid').value.trim();
    if(!taskId)throw Error('请填写任务号');
    st.textContent='正在回查并下载…';st.className='exec-copy-status';
    const r=await api('/studio/api/studio/projects/'+videoProject.id+'/pull',{task_id:taskId});
    if(r&&r.ok){
      ok('已取回视频'+(r.saved_path?'：'+r.saved_path:''));
      videoProject=await api('/studio/api/studio/projects/'+videoProject.id);
      renderVideo();renderSide();
    }else{
      st.textContent='取回失败：'+((r&&r.state)||'服务端未完成');st.className='exec-copy-status err';
    }
  }catch(e){st.textContent='取回失败：'+e.message;st.className='exec-copy-status err';}
};

$('video-pick').onclick=async()=>{
  const st=$('video-status');
  st.textContent='正在打开文件夹选择框…';st.className='exec-copy-status';
  try{
    const r=await api('/studio/api/studio/pick-folder',{initial:$('video-output').value||''});
    if(r.path){$('video-output').value=r.path;ok('下载文件夹：'+r.path);}
    else st.textContent='已取消';
  }catch(e){st.textContent='选择失败：'+e.message;st.className='exec-copy-status err';}
};
$('video-open').onclick=async()=>{
  try{
    await api('/studio/api/studio/open-folder',{initial:$('video-output').value||''});
  }catch(e){feedback('打开文件夹失败：'+e.message,'err');}
};

$('video-make').onclick=async()=>{
  try{
    const topic=$('video-topic').value.trim();
    const count=Number($('video-count').value),duration=Number($('video-duration').value);
    const files=refFiles.slice();
    if(!topic)throw Error('请填写话题');
    if(files.length>9)throw Error('参考图最多 9 张');
    if(!(count>=1&&count<=20))throw Error('数量需为 1–20');
    if(!(duration>=5&&duration<=15))throw Error('时长需为 5–15 秒');
    feedback('正在生成提示词…');
    // 已有一个还没出提示词、话题相同的项目就复用它，避免每点一次都多出一条空记录。
    const reuse=videoProject&&videoProject.prompts&&videoProject.prompts.length===0
                && videoProject.topic===topic&&videoProject.count===count
                && files.length===0&&(videoProject.references||[]).length===0;
    let project=videoProject;
    if(!reuse){
      const form=new FormData();
      form.append('topic',topic);form.append('count',String(count));
      form.append('duration',String(duration));
      form.append('resolution',$('video-quality').value);
      form.append('aspect',$('video-aspect').value);
      if($('video-output').value.trim())form.append('output_dir',$('video-output').value.trim());
      for(const f of files)form.append('reference',f);
      const r=await fetch('/studio/api/studio/projects',{method:'POST',body:form});
      const p=await r.json();
      if(!r.ok)throw Error(typeof p.detail==='string'?p.detail:'创建项目失败');
      project=p;
      videoProject=p;
      renderSide();   // 新记录立刻出现在左侧，不然看着像没新增
    }
    videoProject=await api('/studio/api/studio/projects/'+project.id+'/prompts',{});
    renderVideo();renderSide();
    ok('已生成 '+videoProject.prompts.length+' 条提示词');
  }catch(e){feedback('生成失败：'+e.message,'err');}
};
function renderVideo(){
  const box=$('video-list');box.replaceChildren();
  const ready=videoProject?videoProject.prompts.filter(i=>i.status==='ready'):[];
  if(videoProject&&videoProject.prompt_note){
    const note=document.createElement('div');note.className='exec-side-note';
    note.textContent=videoProject.prompt_note;box.append(note);
  }
  $('video-generate').hidden=!videoProject||!videoProject.prompts.some(i=>i.status==='draft');
  renderResults(ready);
  if(!videoProject)return;
  videoProject.prompts.forEach((item,i)=>{
    const row=document.createElement('div');row.className='exec-video-item';
    const head=document.createElement('div');head.className='exec-video-head';
    const labels={draft:'待提交',queued:'本地排队中',running:'正在提交',submitted:'已提交，等待上游状态',downloading:'上游已完成，正在下载',recovering:'正在自动查询或重试下载',ready:'已完成',failed:'上游生成失败',unknown:'结果未知，请核查上游'};
    const upstream={queued:'上游排队中',pending:'上游排队中',processing:'上游生成中',running:'上游生成中',completed:'上游已完成，准备下载',succeeded:'上游已完成，准备下载',failed:'上游生成失败',cancelled:'上游已取消',canceled:'上游已取消',error:'上游异常'};
    const label=item.status==='submitted'?(upstream[item.upstream_status]||labels.submitted):(labels[item.status]||('未知状态：'+item.status));
    const tag=document.createElement('strong');tag.textContent='#'+(i+1)+' '+label;
    head.append(tag);
    const area=document.createElement('textarea');area.rows=4;area.value=item.text;
    area.disabled=item.status!=='draft';
    area.onchange=async()=>{
      try{
        const r=await fetch('/studio/api/studio/projects/'+videoProject.id+'/prompts/'+item.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:area.value})});
        if(!r.ok)throw Error('保存失败');
        ok('提示词已更新');
      }catch(e){feedback(e.message,'err');}
    };
    row.append(head,area);
    if(item.task_id){
      const tid=document.createElement('div');tid.className='exec-video-tid';
      tid.textContent='任务号：'+item.task_id;
      row.append(tid);
    }
    if(item.error){
      const err=document.createElement('div');err.className='exec-video-error';
      err.textContent=item.error;row.append(err);
    }
    box.append(row);
  });
}
function studioFile(path){
  if(!path)return '';
  return path.startsWith('/api/')?'/studio'+path:path;
}
function renderResults(items){
  const box=$('exec-results');if(!box)return;
  box.replaceChildren();
  if(!items||!items.length){box.hidden=true;return;}
  box.hidden=false;
  const title=document.createElement('div');title.className='exec-results-title';
  const folder=videoProject&&videoProject.output_dir;
  title.textContent='已生成视频（'+items.length+' 条）'+(folder?' · 已保存到 '+folder:'');
  const grid=document.createElement('div');grid.className='exec-result-grid';
  box.append(title,grid);
  items.forEach((item,index)=>{
    const card=document.createElement('div');card.className='exec-result';
    const video=document.createElement('video');
    video.controls=true;video.preload='metadata';video.playsInline=true;
    video.src=studioFile(item.video_url);
    const bar=document.createElement('div');bar.className='exec-result-bar';
    const name=document.createElement('span');name.textContent='视频'+(index+1);
    const save=document.createElement('a');
    save.href=studioFile(item.video_url);save.download='视频'+(index+1)+'.mp4';
    save.className='exec-save';save.textContent='另存为';
    bar.append(name,save);
    card.append(video,bar);grid.append(card);
  });
}
function watchVideo(){
  clearTimeout(state.videoTimer);
  if(!videoProject)return;
  const projectId=videoProject.id;
  state.videoTimer=setTimeout(async()=>{
    try{
      const latest=await api('/studio/api/studio/projects/'+projectId);
      if(!videoProject||videoProject.id!==projectId||state.mode!=='video')return;
      videoProject=latest;
      renderVideo();
      if(videoProject.prompts.some(i=>['queued','running','submitted','downloading','recovering'].includes(i.status))){
        feedback('正在自动查询视频状态；完成后自动下载','','video');
        watchVideo();
      }else if(videoProject.prompts.every(i=>i.status==='ready')){
        ok('生成完成；视频已自动下载并显示在下方','video');
      }else{
        feedback('任务已结束或需要核查，请查看各条状态及原因','err','video');
      }
    }catch(e){
      if(videoProject&&videoProject.id===projectId&&state.mode==='video'){
        feedback('读取状态暂时失败，将自动重试：'+e.message,'err','video');watchVideo();
      }
    }
  },4000);
}
$('video-generate').onclick=async()=>{
  try{
    if(!videoProject)throw Error('请先生成提示词');
    if(!window.confirm('将向外部视频服务提交 '+videoProject.prompts.length+' 条生成请求，可能产生费用且不可撤销。确认提交？'))return;
    await api('/studio/api/studio/projects/'+videoProject.id+'/generate',{confirmed:true});
    ok('已提交生成，正在等待结果…');
    videoProject=await api('/studio/api/studio/projects/'+videoProject.id);
    renderVideo();watchVideo();
  }catch(e){feedback('提交失败：'+e.message,'err');}
};

/* ---------- 素材与文案 ---------- */
async function loadAssets(){
  const v=await api('/api/workspace/assets');const old=$('exec-asset').value;
  const sel=$('exec-asset');sel.replaceChildren();option(sel,'','选择作品');
  for(const a of v.assets||[])option(sel,a.id,a.name+' · '+(a.bytes/1048576).toFixed(1)+'MiB');
  if([...sel.options].some(o=>o.value===old))sel.value=old;
  // B站 面板只用素材做「AI 识别参考」，发布本身取手机相册，不共用这个下拉。
  try{fillBiliAssets(v.assets||[]);}catch(e){}
}
$('exec-asset').onchange=()=>{
  const id=$('exec-asset').value;const p=$('exec-preview');const img=$('exec-preview-image');
  // 按文件名后缀决定用 video 还是 img 预览（小红书支持发布图片）
  const name=(()=>{const o=[...$('exec-asset').options].find(o=>o.value===id);return o?o.textContent:'';})();
  const isImage=/\.(jpe?g|png|webp)\b/i.test(name);
  if(img){img.hidden=!(id&&isImage);if(id&&isImage)img.src='/api/workspace/assets/'+id+'/preview';else img.removeAttribute('src');}
  p.hidden=!(id&&!isImage);
  if(id&&!isImage)p.src='/api/workspace/assets/'+id+'/preview';else p.removeAttribute('src');
};
$('exec-upload').onclick=()=>$('exec-file').click();
$('exec-file').onchange=async()=>{
  const file=$('exec-file').files[0];if(!file)return;
  try{
    feedback('正在导入素材…');
    const form=new FormData();form.append('file',file);
    const r=await fetch('/api/workspace/assets',{method:'POST',body:form});
    const v=await r.json();
    if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'导入失败');
    await loadAssets();$('exec-asset').value=v.id;$('exec-asset').onchange();
    ok('已导入：'+v.name);
  }catch(e){feedback('导入失败：'+e.message,'err');}
};
$('exec-copy').onclick=async()=>{
  const btn=$('exec-copy'),st=$('exec-copy-status');const aid=$('exec-asset').value;
  if(btn.disabled)return;
  if(!aid){feedback('请先选择或导入素材','err');return;}
  const platform=$('exec-copy-platform')?$('exec-copy-platform').value:'douyin';
  const isXhs=platform==='xiaohongshu';
  const tagLimit=isXhs?XHS_TAG_LIMIT:5;
  btn.disabled=true;st.textContent='正在读取画面并生成'+(isXhs?'小红书':'抖音')+'文案…';st.className='exec-copy-status';
  try{
    const v=await api('/api/workspace/copy',{asset_id:aid,brief:'',platform});
    $('exec-title').value=v.title||'';
    $('exec-copy-result').value=v.description||'';
    $('exec-tags').value=(v.tags||[]).slice(0,tagLimit).map(t=>t.startsWith('#')?t:'#'+t).join(' ');
    st.textContent='已填入标题／正文／标签（'+(isXhs?'小红书':'抖音')+'风格）';st.className='exec-copy-status ok';
  }catch(e){st.textContent='生成失败：'+e.message;st.className='exec-copy-status err';}
  finally{btn.disabled=false;}
};
$('exec-tags').addEventListener('input',()=>{
  const limit=$('exec-copy-platform')&&$('exec-copy-platform').value==='xiaohongshu'?XHS_TAG_LIMIT:5;
  const list=$('exec-tags').value.split(/\s+/).filter(Boolean);
  if(list.length>limit)$('exec-tags').value=list.slice(0,limit).join(' ');
});

/* ---------- 按手机分配 ---------- */
function renderAssignStatus(){
  const n=[...state.selected].filter(s=>state.assignments.has(s)).length;
  const st=$('exec-assign-status');
  if(!st)return;
  st.textContent=n?'已为 '+n+' 台指定不同视频':'未单独分配，统一用当前视频';
  st.className='exec-copy-status'+(n?' ok':'');
}
$('exec-assign').onclick=async()=>{
  try{
    const phones=[...state.selected].map(deviceOf).filter(Boolean).sort((a,b)=>a.index-b.index);
    if(!phones.length)throw Error('请先选择手机');
    const list=(await api('/api/workspace/assets')).assets||[];
    if(!list.length)throw Error('请先导入视频');
    const dlg=document.createElement('dialog');dlg.className='exec-dialog';
    const h=document.createElement('h3');h.textContent='按手机分配视频';
    const wrap=document.createElement('div');wrap.className='exec-assign-list';
    const selects=new Map();
    for(const p of phones){
      const row=document.createElement('label');
      const name=document.createElement('span');name.textContent=p.index+'号';
      const sel=document.createElement('select');option(sel,'','使用当前视频');
      for(const a of list)option(sel,a.id,a.name);
      sel.value=state.assignments.get(p.serial)||'';
      selects.set(p.serial,sel);row.append(name,sel);wrap.append(row);
    }
    const bar=document.createElement('div');bar.className='exec-run-row';
    const seq=document.createElement('button');seq.type='button';seq.textContent='按顺序分配';
    seq.onclick=()=>[...selects.values()].forEach((s,i)=>{s.value=list[i%list.length].id;});
    const save=document.createElement('button');save.type='button';save.className='exec-primary';save.textContent='保存';
    save.onclick=()=>{
      for(const [sn,sel] of selects){if(sel.value)state.assignments.set(sn,sel.value);else state.assignments.delete(sn);}
      dlg.close();renderAssignStatus();
    };
    const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';
    cancel.onclick=()=>dlg.close();
    bar.append(seq,cancel,save);dlg.append(h,wrap,bar);document.body.append(dlg);
    dlg.addEventListener('close',()=>dlg.remove());
    dlg.showModal();
  }catch(e){feedback('分配失败：'+e.message,'err');}
};

/* ---------- 绑定 ---------- */
$('exec-select-all').onclick=()=>{
  state.devices.filter(d=>d.state==='online').forEach(d=>state.selected.add(d.serial));
  renderPhones();syncObservation();renderAssignStatus();
};
$('exec-clear-selection').onclick=()=>{
  state.selected.clear();renderPhones();syncObservation();renderAssignStatus();
};
$('exec-run-quick').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runQuick();}catch(e){feedback(e.message,'err');}finally{state.busy=false;}
};
$('exec-run-xhs').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runQuickXhs();}catch(e){feedback(e.message,'err');}finally{state.busy=false;}
};
$('exec-run-task').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runTask();}catch(e){feedback(e.message,'err');}finally{state.busy=false;}
};
$('exec-stop').onclick=async()=>{
  if(!state.tasks.length){feedback('当前没有进行中的任务','err');return;}
  for(const id of state.tasks){try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){log('停止失败：'+e.message);}}
  ok('已请求停止；已发出的动作不能撤回');
};
$('lead-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runLead();}catch(e){feedback(e.message,'err','quick');$('lead-status').textContent='';}finally{state.busy=false;}
};
$('lead-reply').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runLeadReply();}catch(e){feedback(e.message,'err','quick');$('lead-status').textContent='';}finally{state.busy=false;}
};
$('lead-stop').onclick=()=>{leadReplyRunning=false;stopLead();};
$('xhs-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runXhsLead();}catch(e){feedback(e.message,'err','quick');$('xhs-status').textContent='';}finally{state.busy=false;}
};
$('xhs-stop').onclick=()=>stopXhsLead();
$('bl-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runBiliLead();}catch(e){feedback(e.message,'err','quick');$('bl-status').textContent='';}finally{state.busy=false;}
};
$('bl-stop').onclick=()=>stopBiliLead();

/* ---------- B站发布 ---------- */
function biliSerialize(){
  const serials=[...state.selected];
  if(!serials.length)throw Error('请先选择手机');
  const title=$('bili-title').value.trim();
  if(!title)throw Error('请填写视频标题');
  if(title.length>80)throw Error('标题有 '+title.length+' 字，超过 B站 80 字上限');
  const desc=$('bili-desc').value.trim();
  if(desc.length>2000)throw Error('简介有 '+desc.length+' 字，超过 2000 字上限');
  const album=parseInt($('bili-album').value||'0',10)||0;
  return {serials,title,desc,album};
}
async function runBili(){
  const st=$('bili-status');
  const p=biliSerialize();
  st.textContent='正在下发…';st.className='exec-copy-status';
  let okCount=0;
  // 关键：创建出来的任务 id 必须登记进 state.tasks 并启动 poll()，
  // 否则执行记录会永远停在"执行中"，到 awaiting_approval 也不会渲染「确认」按钮，
  // 180 秒确认窗口直接被耗光（2026-11-02 实测踩过）。
  const created=[];
  const batch=(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random());
  for(const serial of p.serials){
    try{
      const r=await api('/api/execution/tasks',{
        serial,goal:'执行B站视频发布：'+p.title.slice(0,20),
        script_id:BILI_SKILL,title:p.title,caption:p.desc,
        album_index:p.album,quick_entry_id:BILI_SKILL,quick_entry_name:'B站视频发布',
        quick_batch_id:batch
      });
      if(r&&r.id){okCount++;created.push(r.id);}
    }catch(e){
      st.textContent=numberLabel(serial)+' 下发失败：'+e.message;st.className='exec-copy-status err';
    }
  }
  if(okCount){
    st.textContent='已下发 '+okCount+' 台，去执行记录看进度';st.className='exec-copy-status ok';
    state.quickView='bili';state.taskMode='quick';
    state.tasks=created;
    quickRecordId=batch;
    renderSide();
    refreshQuickHistory();
    poll();
  }else if(!st.textContent.includes('失败')){
    st.textContent='没有下发成功的任务';st.className='exec-copy-status err';
  }
}
$('bili-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runBili();}catch(e){$('bili-status').textContent=e.message;$('bili-status').className='exec-copy-status err';}finally{state.busy=false;}
};
$('bili-copy').onclick=async()=>{
  const btn=$('bili-copy'),st=$('bili-copy-status'),aid=$('bili-ref').value;
  if(!aid){st.textContent='请先选择“AI 识别参考素材”';st.className='exec-copy-status err';return;}
  btn.disabled=true;st.textContent='正在读取画面并生成…';st.className='exec-copy-status';
  try{
    const v=await api('/api/workspace/copy',{asset_id:aid,platform:'bilibili',brief:$('bili-title').value.trim()});
    $('bili-title').value=(v.title||'').slice(0,80);
    $('bili-desc').value=(v.description||'')+((v.tags&&v.tags.length)?('\n\n'+v.tags.map(t=>'#'+t).join(' ')):'');
    st.textContent='已填入标题／简介（标签已附在简介末尾，B站分区标签会自动推荐）';st.className='exec-copy-status ok';
  }catch(e){st.textContent='生成失败：'+e.message;st.className='exec-copy-status err';}
  finally{btn.disabled=false;}
};
function fillBiliAssets(rows){
  const sel=$('bili-ref');if(!sel)return;
  const keep=sel.value;
  sel.replaceChildren();
  const none=document.createElement('option');none.value='';none.textContent='不生成，手写文案';sel.append(none);
  for(const r of rows){const o=document.createElement('option');o.value=r.id;o.textContent=r.name;sel.append(o);}
  if(keep)sel.value=keep;
}
/* ---------- 右侧实时画面 ---------- */
function picture(shot){
  if(!shot)return;
  const img=$('exec-image'),empty=$('exec-observe-empty'),info=$('exec-shot-info');
  if(shot.image){img.src=shot.image;img.hidden=false;empty.hidden=true;}
  else{img.hidden=true;empty.hidden=false;}
  const at=shot.at?new Date(shot.at*1000).toLocaleTimeString():'';
  info.textContent=[at,(shot.width&&shot.height)?shot.width+'×'+shot.height:'',shot.app||''].filter(Boolean).join(' · ')||'已捕捉';
}
// 观察手机：选完直接开实时大屏（已删除下方按钮，下拉即切换）。
let __execObserveBusy=false;
$('exec-device').onchange=async()=>{
  if(__execObserveBusy)return;
  const sn=$('exec-device').value;
  try{window.closeAiBigscreen?.();}catch(e){}
  if(!sn){
    $('exec-observe-empty').hidden=false;
    $('exec-observe-empty').textContent='选一台手机，右侧会直接显示实时大屏';
    const im=$('exec-image');if(im)im.hidden=true;
    const si=$('exec-shot-info');if(si)si.textContent='未捕捉画面';
    return;
  }
  __execObserveBusy=true;
  $('exec-device').disabled=true;
  $('exec-observe-empty').textContent='正在打开 '+numberLabel(sn)+' 的实时大屏…';
  $('exec-observe-empty').hidden=false;
  try{
    if(typeof window.openAiBigscreen==='function'&&await window.openAiBigscreen(sn)){
      $('exec-observe-empty').hidden=true;
      feedback('观察：'+numberLabel(sn)+'（实时大屏已打开）');
    }else{
      $('exec-observe-empty').textContent='实时大屏入口不可用，请重新选择手机';
    }
  }catch(e){feedback('打开实时大屏失败：'+e.message,'err');}
  finally{$('exec-device').disabled=false;__execObserveBusy=false;}
};
/* ---------- 截流数据 ---------- */
const LEAD_PLATFORM_NAMES={douyin:'抖音',redbook:'小红书',bilibili:'B站',instagram:'Instagram',x:'X',facebook:'Facebook',tiktok:'TikTok'};
const LEAD_STATE_NAMES={pending:'待办',drafted:'已起草',claimed:'已认领',sending:'发送中',replied:'已回复',failed:'失败',unknown:'待确认',skipped:'已放弃'};
const LEAD_COLUMNS=['平台','评论人昵称','评论人UID / 账号','评论内容','评论时间','地区','视频/笔记标题','视频/笔记作者','来源作者UID / 账号','状态','认领手机','回复手机','回复结果','回复时间','草稿 / 回复内容','采集时间'];
const leadData={page:0,limit:50,total:0,timer:null,seq:0,pool404:false,
  startDate:'',endDate:'',quick:'today',archived:'active',
  archiveToken:'',archiveInfo:null,exportBusy:false,capacity:null};
function leadTodayShanghai(){
  const now=new Date(Date.now()+(8*60+new Date().getTimezoneOffset())*60000);
  const p=n=>String(n).padStart(2,'0');
  return now.getFullYear()+'-'+p(now.getMonth()+1)+'-'+p(now.getDate());
}
function leadAddDays(iso,n){
  const [y,m,d]=iso.split('-').map(Number);
  const t=new Date(Date.UTC(y,m-1,d)+n*86400000);
  return t.getUTCFullYear()+'-'+String(t.getUTCMonth()+1).padStart(2,'0')+'-'+String(t.getUTCDate()).padStart(2,'0');
}
function leadApplyQuick(q){
  leadData.quick=q;
  if(q==='all'){leadData.startDate='';leadData.endDate='';}
  else{
    const today=leadTodayShanghai();
    if(q==='today'){leadData.startDate=today;leadData.endDate=today;}
    else if(q==='yesterday'){const y=leadAddDays(today,-1);leadData.startDate=y;leadData.endDate=y;}
    else if(q==='week'){leadData.startDate=leadAddDays(today,-6);leadData.endDate=today;}
    else if(q==='month'){leadData.startDate=leadAddDays(today,-29);leadData.endDate=today;}
  }
  leadData.page=0;
}
function leadArchiveNotice(){
  const el=$('lead-data-archive-note');
  if(el)el.textContent='归档只把已完结记录移出日常列表，保留去重信息，不释放磁盘空间；不碰待办、认领中、发送中的任务。';
}

function ensureLeadData(){
  const existed=$('lead-data-mask');
  if(existed&&!existed.__stub)return;
  const mask=document.createElement('div');
  mask.id='lead-data-mask';mask.className='lead-data-mask';mask.hidden=true;
  mask.innerHTML=`<div class="lead-data-panel" role="dialog" aria-label="截流数据">
    <header class="lead-data-head">
      <strong>截流数据</strong>
      <span id="lead-data-summary" class="lead-data-summary">加载中…</span>
      <div class="lead-data-head-actions">
        <button id="lead-data-refresh" type="button">刷新</button>
        <button id="lead-data-close" type="button">关闭</button>
      </div>
    </header>
    <div class="lead-data-filters">
      <label>平台<select id="lead-data-platform">
        <option value="all">全部平台</option>
        <option value="douyin">抖音</option>
        <option value="redbook">小红书</option>
        <option value="bilibili">B站</option>
        <option value="tiktok">TikTok</option>
        <option value="facebook">Facebook</option>
      </select></label>
      <label>状态<select id="lead-data-status">
        <option value="all">全部状态</option>
        <option value="pending">待办</option>
        <option value="drafted">已起草</option>
        <option value="claimed">已认领</option>
        <option value="sending">发送中</option>
        <option value="replied">已回复</option>
        <option value="failed">失败</option>
        <option value="unknown">待确认</option>
        <option value="skipped">已放弃</option>
      </select></label>
      <label>范围<select id="lead-data-archived">
        <option value="active">日常列表（不含已归档）</option>
        <option value="archived">仅已归档</option>
        <option value="all">含已归档</option>
      </select></label>
      <label class="lead-data-search">搜索<input id="lead-data-q" type="search" placeholder="评论人UID / 账号 / 昵称 / 内容 / 标题 / 设备号"></label>
      <span id="lead-data-count" class="lead-data-count"></span>
    </div>
    <div class="lead-data-filters lead-data-datebar">
      <label>日期范围<select id="lead-data-quick">
        <option value="today">今天</option>
        <option value="yesterday">昨天</option>
        <option value="week">近7天</option>
        <option value="month">近30天</option>
        <option value="all">全部日期</option>
        <option value="custom">自定义</option>
      </select></label>
      <label>开始<input id="lead-data-start" type="date"></label>
      <label>结束<input id="lead-data-end" type="date"></label>
      <button id="lead-data-date" type="button">按日期查看（北京时间）</button>
      <button id="lead-data-export" type="button">导出当前筛选CSV</button>
      <button id="lead-data-capacity" type="button">容量</button>
      <span id="lead-data-date-note" class="lead-data-date-note"></span>
    </div>
    <div id="lead-data-capacity-box" class="lead-data-capacity" hidden></div>
    <div class="lead-data-filters lead-data-archive">
      <span class="lead-data-archive-title">手动归档</span>
      <button id="lead-data-archive-preview" type="button">预览可归档</button>
      <button id="lead-data-archive-confirm" type="button" disabled>确认归档</button>
      <span id="lead-data-archive-note" class="lead-data-date-note"></span>
    </div>
    <div class="lead-data-table-wrap">
      <table class="lead-data-table">
        <thead><tr>${LEAD_COLUMNS.map(c=>'<th>'+c+'</th>').join('')}</tr></thead>
        <tbody id="lead-data-body"></tbody>
      </table>
    </div>
    <footer class="lead-data-foot">
      <button id="lead-data-prev" type="button">上一页</button>
      <span id="lead-data-page"></span>
      <button id="lead-data-next" type="button">下一页</button>
    </footer>
  </div>`;
  document.body.append(mask);
  $('lead-data-close').onclick=closeLeadData;
  $('lead-data-refresh').onclick=()=>loadLeadData();
  mask.onclick=(event)=>{if(event.target===mask)closeLeadData();};
  $('lead-data-platform').onchange=()=>{leadData.page=0;leadData.archiveToken='';loadLeadData();};
  $('lead-data-status').onchange=()=>{leadData.page=0;leadData.archiveToken='';loadLeadData();};
  $('lead-data-archived').onchange=()=>{leadData.archived=$('lead-data-archived').value;leadData.page=0;leadData.archiveToken='';loadLeadData();};
  $('lead-data-q').oninput=()=>{
    clearTimeout(leadData.timer);
    leadData.timer=setTimeout(()=>{leadData.page=0;leadData.archiveToken='';loadLeadData();},300);
  };
  $('lead-data-quick').onchange=()=>{
    const q=$('lead-data-quick').value;
    if(q==='custom'){$('lead-data-date-note').textContent='自定义请填写起止日期后点按日期查看。';return;}
    leadApplyQuick(q);
    $('lead-data-start').value=leadData.startDate;$('lead-data-end').value=leadData.endDate;
    $('lead-data-date-note').textContent='';
    leadData.archiveToken='';loadLeadData();
  };
  $('lead-data-date').onclick=()=>{
    leadData.quick=$('lead-data-quick').value;
    if(leadData.quick!=='custom')leadApplyQuick(leadData.quick);
    else{
      leadData.startDate=$('lead-data-start').value;
      leadData.endDate=$('lead-data-end').value;
      if(leadData.startDate&&!leadData.endDate)leadData.endDate=leadData.startDate;
      if(leadData.endDate&&!leadData.startDate)leadData.startDate=leadData.endDate;
    }
    if(leadData.startDate&&leadData.endDate&&leadData.startDate>leadData.endDate){
      $('lead-data-date-note').textContent='开始日期不能晚于结束日期';
      return;
    }
    $('lead-data-start').value=leadData.startDate;$('lead-data-end').value=leadData.endDate;
    $('lead-data-date-note').textContent=leadData.startDate?('当前 '+leadData.startDate+' 至 '+leadData.endDate+'（北京时间入库时间）'):'全部日期';
    leadData.page=0;leadData.archiveToken='';loadLeadData();
  };
  $('lead-data-export').onclick=async()=>{
    const btn=$('lead-data-export');
    if(leadData.exportBusy)return;
    leadData.exportBusy=true;btn.disabled=true;
    try{
      const r=await fetch('/api/lead-pool/export?'+leadPoolParams().toString());
      if(!r.ok){
        let msg='导出失败 '+r.status;
        try{const v=await r.json();if(v&&v.detail)msg=typeof v.detail==='string'?v.detail:JSON.stringify(v.detail);}catch(e){}
        if(r.status===404)msg='导出接口未加载，需授权重启后生效不做旧接口猜测。';
        throw Error(msg);
      }
      const blob=await r.blob();
      const a=document.createElement('a');
      a.href=URL.createObjectURL(blob);a.download='lead-pool.csv';
      document.body.append(a);a.click();a.remove();
      setTimeout(()=>URL.revokeObjectURL(a.href),5000);
    }catch(e){$('lead-data-date-note').textContent=/429/.test(e.message)?'同时导出过多请稍后重试。':e.message;}finally{leadData.exportBusy=false;btn.disabled=false;}
  };
  $('lead-data-capacity').onclick=async()=>{
    try{
      const r=await fetch('/api/lead-pool/capacity');
      let v={};
      try{v=await r.json();}catch(e){}
      if(!r.ok)throw Error((v&&typeof v.detail==='string'?v.detail:v.detail&&JSON.stringify(v.detail))||('请求失败 '+r.status));
      leadShowCapacity(v);
    }catch(e){const box=$('lead-data-capacity-box');if(box){box.hidden=false;box.textContent=e.status===404||/404/.test(e.message)?'容量接口未加载需授权重启后生效。':('容量读取失败：'+e.message);}}
  };
  $('lead-data-archive-preview').onclick=async()=>{
    if(!leadRequireDates())return;
    const btn=$('lead-data-archive-confirm');
    try{
      const data=await api('/api/lead-pool/archive',Object.fromEntries(leadPoolParams({confirm:true,preview:true})));
      leadData.archiveToken=data.token||'';
      leadData.archiveInfo=data;
      btn.disabled=!leadData.archiveToken;
      $('lead-data-archive-note').textContent='可归档 '+data.eligible_count+' 条共 '+Object.entries(data.platforms||{}).map(([p,n])=>(LEAD_PLATFORM_NAMES[p]||p)+n+'条').join('、')+'；只含已回复、失败、已放弃，待办和认领中不会动。确认后再次核对再提交。';
    }catch(e){
      leadData.archiveToken='';btn.disabled=true;
      $('lead-data-archive-note').textContent=/404/.test(e.message)?'归档接口未加载需授权重启后生效。':(/422/.test(e.message)?('可归档过多请缩小日期范围或按平台分批：'+e.message):('预览失败：'+e.message));
    }
  };
  $('lead-data-archive-confirm').onclick=async()=>{
    if(!leadRequireDates()||!leadData.archiveToken)return;
    if(!window.confirm('确认归档？已归档只移出日常列表不删除去重记录不会释放磁盘。'))return;
    try{
      const data=await api('/api/lead-pool/archive',Object.fromEntries(leadPoolParams({confirm:true,preview:false,token:leadData.archiveToken})));
      leadData.archiveToken='';
      $('lead-data-archive-confirm').disabled=true;
      $('lead-data-archive-note').textContent='已归档 '+data.archived_count+' 条（选中 '+data.selected_count+'，跳过 '+data.skipped_count+'）';
      loadLeadData();
    }catch(e){
      $('lead-data-archive-note').textContent=/404/.test(e.message)?'归档接口未加载需授权重启后生效。':('归档失败可先重新预览再提交：'+e.message);
    }
  };
  $('lead-data-prev').onclick=()=>{if(leadData.page>0){leadData.page--;loadLeadData();}};
  $('lead-data-next').onclick=()=>{
    const pages=Math.max(1,Math.ceil(leadData.total/leadData.limit));
    if(leadData.page<pages-1){leadData.page++;loadLeadData();}
  };
  document.addEventListener('keydown',(event)=>{
    if(event.key==='Escape'&&$('lead-data-mask')&&!$('lead-data-mask').hidden)closeLeadData();
  });
}
function fmtLeadTime(ts){
  if(!ts)return '';
  const d=new Date(ts*1000);
  const p=n=>String(n).padStart(2,'0');
  return (d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
function leadCell(text,cls){
  const td=document.createElement('td');
  if(cls)td.className=cls;
  const value=text==null?'':String(text);
  td.title=value;
  if(cls==='lead-col-text'||cls==='lead-col-title'){
    const preview=document.createElement('div');preview.className='lead-cell-preview';preview.textContent=value;td.append(preview);
    if(value.length>60||value.split('\n').length>3){
      const toggle=document.createElement('button');toggle.type='button';toggle.className='lead-cell-toggle';toggle.textContent='展开全文';toggle.setAttribute('aria-expanded','false');
      toggle.onclick=()=>{const expanded=td.classList.toggle('lead-cell-expanded');toggle.textContent=expanded?'收起':'展开全文';toggle.setAttribute('aria-expanded',String(expanded));};
      td.append(toggle);
    }
  }else td.textContent=value;
  return td;
}
function leadIdentity(row){
  // 平台各自取评论人字段，禁止用来源作者或视频ID冒充联系对象。
  const fields={douyin:['commenter_douyin_id','抖音号','author_douyin_id','作者抖音号'],redbook:['author_red_id','小红书号','note_author_red_id','作者小红书号'],bilibili:['commenter_uid','UID','up_uid','UP UID'],tiktok:['commenter_handle','TikTok号','author_handle','作者TikTok号'],facebook:[null,'','post_author','帖子作者']};
  const f=fields[row.platform]||[null,'',null,''];
  const value=f[0]?String(row[f[0]]||'').trim():'',source=f[2]?String(row[f[2]]||'').trim():'';
  if(row.platform==='facebook'){
    const pid=row.commenter_profile_id||'', purl=row.commenter_profile_url||'';
    const val=pid?(pid.indexOf('profile.php')>=0?pid:('ID：'+pid)):'';
    return {commenter:val||'无稳定UID（昵称+帖子定位）',source:purl||''};
  }
  return {commenter:value?f[1]+'：'+value:(row.platform==='facebook'?'无稳定UID（昵称+帖子定位）':'未采集（不可仅凭昵称定位）'),source:f[2]?(source?f[3]+'：'+source:'未采集'):''};
}
const LEAD_EXTRACT_LABELS={found:'已找到并填草稿',not_found_within_budget:'预算内未找到',error:'出错',skipped:'跳过（无需联系）',ambiguous:'候选不唯一'};
function leadExtractLabel(result){
  if(!result)return '';
  return LEAD_EXTRACT_LABELS[result]||result;
}
// 草稿/回复内容：优先 reply_text/dm_text，其次从提取证据里的 draft.input_text 取
function leadDraftText(row){
  const direct=row.reply_text||row.dm_text;
  if(direct)return direct;
  let ev=row.extract_evidence;
  if(typeof ev==='string'){try{ev=JSON.parse(ev);}catch(e){ev=null;}}
  const draft=ev&&ev.draft;
  return (draft&&draft.input_text)||'';
}
// 评论时间：四家用 time_text，Facebook 用 comment_time（2026-11-04 统一取值）
function leadCommentTime(row){
  const v=row.time_text||row.comment_time||'';
  return String(v).trim();
}
function leadRow(row){
  const tr=document.createElement('tr');
  const identity=leadIdentity(row);
  const status=row.status||'';
  const drafted=leadDraftText(row);
  tr.append(
    leadCell(LEAD_PLATFORM_NAMES[row.platform]||row.platform,'lead-col-platform'),
    leadCell(row.comment_author||row.author||'','lead-col-author'),
    leadCell(identity.commenter,'lead-col-small'),
    leadCell(row.comment_text||row.content||'','lead-col-text'),
    leadCell(leadCommentTime(row),'lead-col-small'),
    leadCell(row.region||'','lead-col-small'),
    leadCell(row.video_title||row.note_title||'','lead-col-title'),
    leadCell(row.video_author||row.note_author||row.up_name||'','lead-col-author'),
    leadCell(identity.source,'lead-col-small'),
    leadCell(LEAD_STATE_NAMES[status]||status,'lead-col-state lead-state-'+status),
    leadCell(row.claimed_by||'','lead-col-small'),
    // 回复回执：谁执行、结果、时间、填进去的草稿
    leadCell(row.extracted_by||'','lead-col-small'),
    leadCell(leadExtractLabel(row.extract_result),'lead-col-small'+(row.extract_result==='error'?' lead-state-failed':'')),
    leadCell(row.extracted_at?fmtLeadTime(row.extracted_at):'','lead-col-small'),
    leadCell(drafted,'lead-col-text'),
    leadCell(fmtLeadTime(row.collected_at),'lead-col-small')
  );
  return tr;
}
function leadSummary(stats){
  const agg={pending:0,claimed:0,replied:0,failed:0,unknown:0,skipped:0};
  const per=(stats&&stats.platforms)||{};
  for(const key in per){
    const row=per[key]||{};
    for(const name in agg)agg[name]+=row[name]||0;
  }
  return '待办 '+agg.pending+' · 已认领 '+agg.claimed+' · 已回复 '+agg.replied
    +' · 失败 '+agg.failed+' · 待确认 '+agg.unknown+' · 放弃 '+agg.skipped;
}
function leadLegacyStatus(status){
  if(status==='drafted')return 'claimed';
  return status;
}
function leadPoolParams(extra){
  const params=new URLSearchParams({
    platform:$('lead-data-platform').value,
    status:$('lead-data-status').value,
    archived:leadData.archived,
    q:$('lead-data-q').value.trim(),
    start_date:leadData.startDate,
    end_date:leadData.endDate,
    ...(extra||{})
  });
  return params;
}
function leadLegacyParams(extra){
  return new URLSearchParams({
    platform:$('lead-data-platform').value,
    status:leadLegacyStatus($('lead-data-status').value),
    q:$('lead-data-q').value.trim(),
    start_date:leadData.startDate,
    end_date:leadData.endDate,
    archived:leadData.archived,
    ...(extra||{})
  });
}
async function loadLeadData(){
  ensureLeadData();
  const seq=++leadData.seq;
  const params=leadPoolParams({offset:String(leadData.page*leadData.limit),limit:String(leadData.limit)});
  const body=$('lead-data-body');
  $('lead-data-count').textContent='加载中…';
  try{
    let data;
    if(!leadData.pool404){
      const r=await fetch('/api/lead-pool?'+params.toString());
      if(r.status===404){leadData.pool404=true;}
      else{
        let v={};
        try{v=await r.json();}catch(e){}
        if(!r.ok)throw Error((v&&typeof v.detail==='string'?v.detail:v.detail&&JSON.stringify(v.detail))||('请求失败 '+r.status));
        data=v;
      }
    }
    if(!data){
      data=await api('/api/execution/leads/pool?'+leadLegacyParams({
        offset:String(leadData.page*leadData.limit),limit:String(leadData.limit)}).toString());
      $('lead-data-date-note').textContent='当前为兼容旧服务：新版接口未加载需授权重启后才有导出/归档/准确总数。';
    }
    if(seq!==leadData.seq||$('lead-data-mask').hidden)return;
    leadData.total=data.total||0;
    const pages=Math.max(1,Math.ceil(leadData.total/leadData.limit));
    if(leadData.page>=pages)leadData.page=pages-1;
    if(leadData.page<0)leadData.page=0;
    body.replaceChildren();
    if(!data.rows||!data.rows.length){
      const tr=document.createElement('tr');
      const td=leadCell('没有数据。先到「快捷入口 → 抖音截流」采集一轮。','lead-col-empty');
      td.colSpan=LEAD_COLUMNS.length;tr.append(td);body.append(tr);
    }else{
      for(const row of data.rows)body.append(leadRow(row));
    }
    const active=leadData.archived==='archived'?'（仅看已归档，认领不受影响）':'';
    $('lead-data-summary').textContent=leadSummary(data.stats)+active;
    $('lead-data-count').textContent='共 '+leadData.total+' 条';
    $('lead-data-page').textContent='第 '+(leadData.page+1)+' / '+pages+' 页';
    $('lead-data-prev').disabled=leadData.page<=0;
    $('lead-data-next').disabled=leadData.page>=pages-1;
  }catch(e){
    if(seq!==leadData.seq||$('lead-data-mask').hidden)return;
    body.replaceChildren();
    const tr=document.createElement('tr');
    const td=leadCell('读取失败：'+e.message,'lead-col-empty');
    td.colSpan=LEAD_COLUMNS.length;tr.append(td);body.append(tr);
    $('lead-data-summary').textContent='读取失败';
  }
}
function leadShowCapacity(info){
  const box=$('lead-data-capacity-box');
  if(!box)return;
  const mb=v=>(Number(v||0)/1048576).toFixed(1)+' MiB';
  const rows=Object.keys(info.platforms||{}).map(p=>{
    const c=info.platforms[p];
    return (LEAD_PLATFORM_NAMES[p]||p)+'：日常 '+c.active+' / 已归档 '+c.archived+' / 共 '+c.total;
  }).join('；');
  box.hidden=false;
  box.textContent='数据库 '+mb(info.main_bytes)+'（WAL '+mb(info.wal_bytes)+'），合计 '+mb(info.total_bytes)+'；磁盘剩余 '+mb(info.disk_free_bytes)+'。'+rows+'归档不释放空间，只移出日常列表。';
}
function leadRequireDates(){
  if(!leadData.startDate||!leadData.endDate){
    $('lead-data-archive-note').textContent='归档必须选择起止日期，不能用全部日期。';
    return false;
  }
  return true;
}
function leadSyncFilterUI(){
  $('lead-data-quick').value=leadData.quick;
  $('lead-data-start').value=leadData.startDate;
  $('lead-data-end').value=leadData.endDate;
  $('lead-data-archived').value=leadData.archived;
  $('lead-data-date-note').textContent=leadData.startDate?('当前 '+leadData.startDate+' 至 '+leadData.endDate+'（北京时间入库时间）'):'';
}
function openLeadData(){
  ensureLeadData();
  leadApplyQuick(leadData.quick);
  leadSyncFilterUI();
  leadArchiveNotice();
  $('lead-data-mask').hidden=false;
  document.body.classList.add('lead-data-open');
  leadData.page=0;
  loadLeadData();
}
function closeLeadData(){
  const mask=$('lead-data-mask');
  if(mask)mask.hidden=true;
  document.body.classList.remove('lead-data-open');
}
$('lead-data-open').onclick=()=>openLeadData();

document.querySelectorAll('#exec-modes button').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
$('chat-config').onclick=()=>{if(typeof window.openAiPanel==='function')window.openAiPanel();else log('模型设置暂不可用');};
$('exec-expand').onclick=()=>{
  window.closeAiBigscreen?.();
  chat.classList.add('hidden');
  document.querySelector('.rp-tab[data-tab="common"]')?.click();
  document.getElementById('btn-open-ai')?.focus();
};
window.openExecutionWorkspace=()=>{
  chat.classList.remove('hidden');
  refresh().catch(e=>log(e.message));
  loadAssets().catch(e=>log(e.message));
  setMode(state.mode);
  $('exec-title')?.focus();
};

/* ================================================================
 * 2026-11-03 重构：发布视频 / 截流 两个合并面板
 *  - 顶部选平台，各平台字段与校验互相独立
 *  - 采集只按「采集评论总数」，采集与回复完全解耦
 *  - 支持多素材、排序、删除、按素材分配手机
 *  - 执行记录：详情 / 单独回复 / 再次采集 / 删除 / 停止
 * ================================================================ */

const PUB_PLATFORMS={
  douyin:{name:'抖音',skill:QUICK_SKILL,entry:'builtin:douyin-publish',entryName:'抖音视频发布',
          titleLimit:20,descLimit:1000,tagLimit:5,album:false,
          titlePh:'抖音标题不超过 20 字',descPh:'作品描述（口播/直给，一两句即可）',
          tagsPh:'话题最多 5 个，空格分隔，不带 #',
          help:'抖音发布：系统分享直达编辑页 → 填标题/描述/话题 → 回读校验 → 等你确认后才点「发作品」。'},
  xiaohongshu:{name:'小红书',skill:XHS_SKILL,entry:'builtin:xiaohongshu-publish',entryName:'小红书发布',
          titleLimit:XHS_TITLE_LIMIT,descLimit:XHS_BODY_LIMIT,tagLimit:XHS_TAG_LIMIT,album:false,
          titlePh:'小红书标题不超过 20 字',descPh:'正文（种草口吻，可分段落，支持 emoji）',
          tagsPh:'话题最多 10 个，空格分隔，不带 #',
          help:'小红书发布：分享入口打开笔记页 → 填标题/正文/话题 → 等你确认后才点「发布笔记」；支持图片素材。'},
  bilibili:{name:'B站',skill:BILI_SKILL,entry:'builtin:bilibili-publish',entryName:'B站视频发布',
          titleLimit:80,descLimit:2000,tagLimit:0,album:true,
          titlePh:'B站标题不超过 80 字',descPh:'简介（不超过 2000 字，可空）',
          tagsPh:'',
          help:'B站发布：首页「+」→ 相册选第 N 个素材 → 编辑器下一步 → 投稿页填标题/简介 → 等你确认后才发布。素材取手机相册，不走「导入素材」。'},
  facebook:{name:'Facebook',skill:'builtin:facebook-publish',entry:'builtin:facebook-publish',entryName:'Facebook发布',
          titleLimit:0,descLimit:60000,tagLimit:0,album:true,
          titlePh:'Facebook 没有独立标题，写在正文里',
          descPh:'正文（可直接写中文，也可用 AI 按平台风格生成）',
          tagsPh:'',
          help:'Facebook 发布：首页「在想些什麼？」→ 圖庫选素材 → 繼續 → 填正文 → 下一步 → 预览页识别「發佈」。**停在發佈前，不会自动发布**；收尾会捨棄草稿。检查发布前确认分享对象（默认「朋友」）。'},
  tiktok:{name:'TikTok',skill:'builtin:tiktok-publish',entry:'builtin:tiktok-publish',entryName:'TikTok视频发布',
          titleLimit:2200,descLimit:2200,tagLimit:30,album:true,
          titlePh:'TikTok 描述（最多 2200 字）',descPh:'描述（可加 #话题）',
          tagsPh:'话题最多 30 个，空格分隔，不带 #',
          help:'TikTok 发布：首页「创建」→ 相册选第 N 个素材 → 两次「下一步」→ 填描述 → 停在发布按钮前，不会自动发布；收尾会保存为草稿。'}
};
state.pubPlatform='douyin';
state.pubAssets=[];            // [{id,name,bytes}]
state.pubCurrent='';           // 当前正在编辑文案的素材
state.pubCopies=new Map();     // assetId -> {title,description,tags}
state.pubAssign=new Map();     // assetId -> Set(serial)
state.leadxPlatform='douyin';
state.leadxCollect=new Set();
state.leadxReply=new Set();

function pubCfg(){return PUB_PLATFORMS[state.pubPlatform]||PUB_PLATFORMS.douyin;}
function pubPlatformSkill(){return pubCfg().skill;}

/* ---------- 发布：平台切换 ---------- */
function renderPubPlatform(){
  const cfg=pubCfg();
  document.querySelectorAll('#pub-platform-tabs button').forEach(b=>b.classList.toggle('on',b.dataset.platform===state.pubPlatform));
  const t=$('pub-title'),d=$('pub-desc'),tg=$('pub-tags');
  if(t){t.maxLength=cfg.titleLimit;t.placeholder=cfg.titlePh;}
  if(d){d.placeholder=cfg.descPh;}
  if(tg){tg.placeholder=cfg.tagsPh;tg.parentElement.hidden=(cfg.tagLimit===0);}
  const album=$('pub-album-wrap');if(album)album.hidden=true;
  const list=$('pub-assets');if(list)list.parentElement.hidden=false;
  const help=$('pub-help');if(help)help.textContent=cfg.help;
  const run=$('pub-run');if(run)run.textContent='发布到'+cfg.name;
}
function newPublishTask(){
  state.pubAssets=[];state.pubCurrent='';state.pubCopies.clear();state.pubAssign.clear();
  for(const id of ['pub-title','pub-desc','pub-tags','pub-status']){const el=$(id);if(el)el.value='';}
  renderPubPlatform();renderPubAssets();renderPubAssign();
  setMode('quick');
  pubLoadLibrary();
  feedback('发布流程：选平台 → 勾手机 → 选素材 → 填文案','','quick');
}
// 素材库只进下拉框，不直接铺到下方；下方只放本次选中的（导入/加入的）。
state.pubLibrary=[];
async function pubLoadLibrary(){
  try{
    const v=await api('/api/workspace/assets');
    state.pubLibrary=(v.assets||[]).map(a=>({id:a.id,name:a.name||a.filename,thumb_url:a.thumb_url||('')}));
    const sel=$('pub-library');
    if(sel){
      const keep=sel.value;
      sel.replaceChildren();
      const none=document.createElement('option');none.value='';none.textContent='从素材库选择…（共 '+state.pubLibrary.length+' 个，只加选中的）';sel.append(none);
      for(const a of state.pubLibrary){const o=document.createElement('option');o.value=a.id;o.textContent=a.name;sel.append(o);}
      if(keep)sel.value=keep;
    }
  }catch(e){}
}
function pubLibraryAdd(){
  const sel=$('pub-library');if(!sel||!sel.value){feedback('请先从素材库下拉里选一个视频','err');return;}
  const found=state.pubLibrary.find(a=>a.id===sel.value);
  if(!found)return;
  if(!state.pubAssets.some(a=>a.id===found.id))state.pubAssets.push({id:found.id,name:found.name,thumb_url:found.thumb_url});
  state.pubCurrent=found.id;
  pubLoadCopy();renderPubAssets();renderPubAssign();
  ok('已加入：'+found.name);
}

/* ---------- 发布：素材多选列表（缩略图网格，与视频生成下方排布一致） ---------- */
function pubPreviewAsset(a){
  // 界面内预览：视频可播放，图片放大看；点遮罩/✕/Esc 关闭
  try{pubFlushCopy();}catch(e){}
  const old=document.querySelector('#rp-chat .pub-asset-lightbox');
  if(old)old.remove();
  const chat=$('rp-chat')||document.body;
  const lb=document.createElement('div');lb.className='pub-asset-lightbox';
  const isImage=/\.(jpe?g|png|webp)$/i.test(a.name||'');
  let media;
  if(isImage){
    media=document.createElement('img');media.alt=a.name||'';
    media.src='/api/workspace/assets/'+a.id+'/preview';
  }else{
    media=document.createElement('video');
    media.controls=true;media.autoplay=true;media.playsInline=true;
    media.src='/api/workspace/assets/'+a.id+'/preview';
  }
  media.title=a.name||'';
  const close=document.createElement('button');close.type='button';close.className='pub-asset-lightbox-close';
  close.textContent='✕';close.title='关闭预览';
  close.onclick=()=>{try{media.pause?.();}catch(e){} lb.remove();};
  lb.onclick=(e)=>{if(e.target===lb)close.onclick();};
  lb.append(media,close);
  chat.append(lb);
  const esc=(e)=>{if(e.key==='Escape'){close.onclick();document.removeEventListener('keydown',esc);}};
  document.addEventListener('keydown',esc);
}
let __pubDragId=null;
function renderPubAssets(){
  const box=$('pub-assets');if(!box)return;
  box.replaceChildren();
  if(!state.pubAssets.length){
    const tip=document.createElement('div');tip.className='exec-side-note';
    tip.textContent='下方只显示本次选中的素材（缩略图）。点「导入素材 / 导入文件夹」或从素材库下拉选一个点「加入」。';
    box.append(tip);return;
  }
  state.pubAssets.forEach((a,i)=>{
    // 与视频生成下方完全同构：exec-result 卡 + 原比例 video/img，点画面即播，不裁剪
    const card=document.createElement('div');
    card.className='pub-asset-card'+(a.id===state.pubCurrent?' on':'');
    card.title=(a.name||'')+'（拖动可排序）';
    card.draggable=true;card.dataset.aid=a.id;
    const pick=()=>{if(state.pubCurrent===a.id)return;pubFlushCopy();state.pubCurrent=a.id;pubLoadCopy();renderPubAssets();};
    // 卡片空白处点选；视频控件/按钮的点击不触发选中，避免打断播放
    card.addEventListener('click',(e)=>{
      if(e.target.closest('button'))return;
      pick();
    });
    card.addEventListener('dragstart',(e)=>{
      __pubDragId=a.id;
      card.classList.add('dragging');
      try{e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',a.id);}catch(err){}
    });
    card.addEventListener('dragend',()=>{__pubDragId=null;box.querySelectorAll('.dragging,.over').forEach(n=>n.classList.remove('dragging','over'));});
    card.addEventListener('dragover',(e)=>{
      if(!__pubDragId||__pubDragId===a.id)return;
      e.preventDefault();
      try{e.dataTransfer.dropEffect='move';}catch(err){}
      card.classList.add('over');
    });
    card.addEventListener('dragleave',()=>card.classList.remove('over'));
    card.addEventListener('drop',(e)=>{
      e.preventDefault();
      const from=state.pubAssets.findIndex(x=>x.id===__pubDragId);
      const to=state.pubAssets.findIndex(x=>x.id===a.id);
      __pubDragId=null;
      if(from<0||to<0||from===to){renderPubAssets();return;}
      pubFlushCopy();
      const mv=state.pubAssets.splice(from,1)[0];
      state.pubAssets.splice(to,0,mv);
      renderPubAssets();renderPubAssign();
    });
    const wrap=card;
    const isImage=/\.(jpe?g|png|webp)$/i.test(a.name||'');
    const label=(isImage?'图片':'视频')+(i+1);
    // 全帧缩略图：img 高度自动=图片有多高显示多高，不可能被截断；
    // 播放走 ▶ 大窗（video 标签只在有元数据前高度会塌，绝不在格子里放 video）。
    const th=document.createElement('img');
    th.loading='lazy';th.alt=label;th.draggable=false;
    th.src=isImage?('/api/workspace/assets/'+a.id+'/preview'):('/api/workspace/assets/'+a.id+'/thumb');
    const noTh=document.createElement('div');noTh.className='pub-asset-nothumb';noTh.hidden=true;
    noTh.textContent='暂无缩略图，点 ▶ 预览正片';
    th.onerror=()=>{th.style.display='none';noTh.hidden=false;};
    wrap.append(th,noTh);
    // ▶ 悬浮键：在界面内大窗预览（视频播放/图片放大）
    const play=document.createElement('button');play.type='button';play.className='pub-asset-play';
    play.textContent='▶';play.title='大窗预览'+label+'（'+(a.name||a.id)+'）';
    play.onclick=(e)=>{e.stopPropagation();pick();pubPreviewAsset(a);};
    wrap.append(play);
    const tag=document.createElement('span');tag.className='pub-asset-label';
    tag.textContent=label+(state.pubCopies.has(a.id)?' · 已生成文案':'');
    tag.title=a.name||a.id;
    wrap.append(tag);
    if(a.id===state.pubCurrent){
      const cur=document.createElement('span');cur.className='pub-asset-current';
      cur.textContent='当前';wrap.append(cur);
    }
    const del=document.createElement('button');del.type='button';del.className='pub-asset-remove';
    del.textContent='✕';del.title='从本次发布移除（不删素材库）';
    del.onclick=(e)=>{
      e.stopPropagation();
      state.pubAssets=state.pubAssets.filter(x=>x.id!==a.id);
      state.pubCopies.delete(a.id);state.pubAssign.delete(a.id);
      if(state.pubCurrent===a.id)state.pubCurrent=(state.pubAssets[0]||{}).id||'';
      renderPubAssets();renderPubAssign();
    };
    wrap.append(del);
    box.append(card);
  });
}
function pubFlushCopy(){
  // 把输入框写回当前素材的文案缓存（多素材各自独立）
  if(!state.pubCurrent)return;
  state.pubCopies.set(state.pubCurrent,{
    title:$('pub-title').value.trim(),
    description:$('pub-desc').value.trim(),
    tags:splitWords($('pub-tags').value)
  });
}
function pubLoadCopy(){
  const c=state.pubCopies.get(state.pubCurrent);
  $('pub-title').value=c?c.title:'';
  $('pub-desc').value=c?c.description:'';
  $('pub-tags').value=c?(c.tags||[]).join(' '):'';
}
async function pubAddAssets(files){
  for(const file of files){
    try{
      const form=new FormData();form.append('file',file);
      const r=await fetch('/api/workspace/assets',{method:'POST',body:form});
      const v=await r.json();
      if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'导入失败');
      if(!state.pubAssets.some(a=>a.id===v.id))state.pubAssets.push({id:v.id,name:v.name,bytes:v.bytes,thumb_url:v.thumb_url||('')});
      if(!state.pubCurrent)state.pubCurrent=v.id;
    }catch(e){feedback('导入失败：'+e.message,'err');}
  }
  renderPubAssets();renderPubAssign();
  pubLoadLibrary();
  ok('已导入 '+files.length+' 个素材（下方只显示本次选中的）');
}

/* ---------- 发布：AI 生成文案（多素材批量，每素材一套且互不相同） ---------- */
async function pubGenerateCopy(){
  const btn=$('pub-copy'),st=$('pub-copy-status');
  if(btn.disabled)return;
  // 2026-11-06 修：原来写死「B站发布用手机相册素材…」，但 album=true 的其实是
  // B站 / Facebook / TikTok **三家**，于是 TK/FB 点生成也会弹 B站 的提示（主人当场发现）。
  // 现在按当前平台取名字，并把「为什么不能生成」讲清楚：这个接口是**看画面**出文案，
  // 相册素材在电脑侧没有文件、提不出帧，所以不是"不需要"，是"取不到画面"。
  if(pubCfg().album){
    feedback(pubCfg().name+'发布取手机相册素材，电脑侧没有对应视频文件、无法按画面生成文案；'
      +'可直接手写，或先把视频导入素材库再点生成','err');
    return;
  }
  if(!state.pubAssets.length){feedback('请先导入素材','err');return;}
  btn.disabled=true;
  try{
    const cfg=pubCfg();
    // 批量：对每个素材各生成一套；单素材时就是当前选中的那一个
    const targets = state.pubAssets.length>1 ? state.pubAssets : [state.pubAssets.find(a=>a.id===state.pubCurrent)||state.pubAssets[0]];
    const seenTitles = new Set([...state.pubCopies.values()].map(c=>(c.title||'').trim()).filter(Boolean));
    let done = 0;
    for(const a of targets){
      st.textContent=`正在生成 ${done+1}/${targets.length}：${a.name}…`;st.className='exec-copy-status';
      let v=null, tries=0;
      while(tries<3){
        tries++;
        const avoid=[...seenTitles].slice(-6).join(' / ');
        const brief = avoid ? `要求：标题和正文必须与之前不同，不要重复。已有的标题：${avoid}` : '';
        v=await api('/api/workspace/copy',{asset_id:a.id,brief,platform:state.pubPlatform});
        const t=((v.title||'').slice(0,cfg.titleLimit)||'').trim();
        if(!t||!seenTitles.has(t))break;
        v=null; // 重复则重试
      }
      if(!v)throw Error('文案重复多次，请换个素材或手动改一句再试');
      const rec={
        title:(v.title||'').slice(0,cfg.titleLimit),
        description:v.description||'',
        tags:(v.tags||[]).slice(0,cfg.tagLimit||5)
      };
      state.pubCopies.set(a.id,rec);
      if((rec.title||'').trim())seenTitles.add(rec.title.trim());
      done++;
    }
    state.pubCurrent=(targets[0]||{}).id||state.pubCurrent;
    pubLoadCopy();renderPubAssets();renderPubAssign();
    st.textContent=`已生成 ${done} 套文案（各不相同），点素材可切换查看`;st.className='exec-copy-status ok';
  }catch(e){st.textContent='生成失败：'+e.message;st.className='exec-copy-status err';}
  finally{btn.disabled=false;}
}

/* ---------- 发布：按素材分配手机（手机→素材，一个手机只归一个素材） ---------- */
function renderPubAssign(){
  const panel=$('pub-assign-panel');if(!panel)return;
  const st=$('pub-assign-status');
  let n=0;for(const s of state.pubAssign.values())n+=s.size;
  if(st){st.textContent=n?'已为 '+n+' 台手机指定了素材':'未分配：所有勾选手机发同一个素材';st.className='exec-copy-status'+(n?' ok':'');}
  panel.replaceChildren();
  if(panel.hidden)return;
  if(!state.pubAssets.length){panel.append(leadHint('先导入素材，再按素材分配手机。'));return;}
  // 随机/顺序分配：把勾选手机按顺序轮流分给素材，省得一台台点
  const bar=document.createElement('div');bar.className='exec-run-row';
  const rnd=document.createElement('button');rnd.type='button';rnd.textContent='按顺序分配勾选手机';
  rnd.onclick=()=>{
    const phones=[...state.selected];
    if(!phones.length){feedback('请先在顶部勾选手机','err');return;}
    state.pubAssign.clear();
    phones.forEach((sn,i)=>{
      const a=state.pubAssets[i%state.pubAssets.length];
      const set=state.pubAssign.get(a.id)||new Set();set.add(sn);state.pubAssign.set(a.id,set);
    });
    renderPubAssign();ok('已按顺序把 '+phones.length+' 台手机分给 '+state.pubAssets.length+' 个素材');
  };
  const clr=document.createElement('button');clr.type='button';clr.textContent='清空分配';
  clr.onclick=()=>{state.pubAssign.clear();renderPubAssign();};
  bar.append(rnd,clr);panel.append(bar);
  for(const a of state.pubAssets){
    const card=document.createElement('div');card.className='pub-assign-card';
    const head=document.createElement('div');head.className='pub-assign-head';
    const t=document.createElement('strong');t.textContent=a.name;t.title=a.name;
    const cnt=document.createElement('span');cnt.className='exec-copy-status';
    const set=state.pubAssign.get(a.id)||new Set();
    cnt.textContent=set.size+' 台';
    head.append(t,cnt);
    const grid=document.createElement('div');grid.className='exec-phone-grid';
    for(const d of state.devices){
      const b=document.createElement('button');b.type='button';
      b.className='exec-phone-number'+(set.has(d.serial)?' on':'');
      b.textContent=String(d.index);b.title=d.index+'号 · '+(d.name||d.model||'')+' '+d.serial;
      b.disabled=d.state!=='online';
      b.onclick=()=>{
        const cur=state.pubAssign.get(a.id)||new Set();
        if(cur.has(d.serial)){cur.delete(d.serial);}
        else{
          // 同一台手机被别的素材占用：先问是否交换
          let owner=null;
          for(const [aid,s] of state.pubAssign){if(aid!==a.id&&s.has(d.serial))owner=aid;}
          if(owner){
            const other=state.pubAssets.find(x=>x.id===owner);
            if(!window.confirm('手机 '+d.index+' 已分配给「'+(other?other.name:'另一个素材')+'」，要交换过来吗？'))return;
            state.pubAssign.get(owner).delete(d.serial);
          }
          cur.add(d.serial);
        }
        state.pubAssign.set(a.id,cur);
        renderPubAssign();
      };
      grid.append(b);
    }
    card.append(head,grid);panel.append(card);
  }
}

/* ---------- 发布：下达 ---------- */
async function runPublish(){
  const cfg=pubCfg();
  const serials=[...state.selected];
  if(!serials.length)throw Error('请先勾选要发布的手机');
  pubFlushCopy();

  if(cfg.album){
    // B站/TK/FB统一链路：导入素材 → 自动传入手机相册（最新一条）→ 按多手机多素材批量发布
    // 兼容老用法：若未导入素材，则仍按相册第N个发布
    const title=$('pub-title').value.trim(),desc=$('pub-desc').value.trim();
    if(state.pubAssets.length){
      pubFlushCopy();
      const jobs=[];
      let usedAssign=false;
      for(const [aid,set] of state.pubAssign){if(set.size){usedAssign=true;for(const sn of set)jobs.push({serial:sn,assetId:aid});}}
      if(!usedAssign){
        if(!state.pubCurrent)throw Error('请先选择或导入素材');
        for(const sn of serials)jobs.push({serial:sn,assetId:state.pubCurrent});
      }
      const st=$('pub-status');if(st){st.textContent='正在把 '+jobs.length+' 条素材传入手机相册…';st.className='exec-copy-status';}
      for(const j of jobs){
        const c=state.pubCopies.get(j.assetId)||{title:'',description:'',tags:[]};
        const t0=((j.assetId===state.pubCurrent)?title:(c.title||'')).trim()||title;
        const d0=((j.assetId===state.pubCurrent)?desc:(c.description||'')).trim()||desc;
        // 2026-11-06 修：Facebook 是「无独立标题」平台（titleLimit=0，正文即内容），
        // facebook_skill.run_publish 只读 caption、根本不读 title。
        // 这里却强制校验标题非空，于是主人在正文里写好内容后，
        // 仍被拦下报「素材还没有标题」——校验规则和平台设计互相打架。
        // 只有 titleLimit>0 的平台（B站 80 / TK 2200）才要求标题。
        if(!t0 && cfg.titleLimit>0)throw Error(numberLabel(j.serial)+'：素材还没有标题');
        if(t0.length>cfg.titleLimit&&cfg.titleLimit)throw Error(numberLabel(j.serial)+'：标题超限');
        if(!d0 && cfg.titleLimit===0)throw Error(numberLabel(j.serial)+'：正文不能为空（本平台没有独立标题，内容写在正文里）');
        if(d0.length>cfg.descLimit)throw Error(numberLabel(j.serial)+'：正文超限');
        const tr=await api('/api/workspace/transfer',{asset_id:j.assetId,serial:j.serial,confirmed:true});
        if(!tr.ok)throw Error(numberLabel(j.serial)+' 素材传入失败：'+(tr.error||'未知'));
        j.title=t0;j.caption=d0;j.tags=(c.tags||[]).slice(0,cfg.tagLimit||0);
      }
      const plan=jobs.map(j=>({serial:j.serial,title:j.title,caption:j.caption,tags:j.tags,album_index:0,asset_id:j.assetId}));
      await startTasks(plan,cfg.name+'发布：'+(plan[0].title||'').slice(0,16),cfg.skill,{id:cfg.entry,name:cfg.entryName});
      pubClearForm();
      openDetailForIds(state.tasks.slice(),cfg.name+'发布');
      return;
    }
    // 同上：无独立标题的平台（Facebook）只要求正文，不要求标题。
    if(!title && cfg.titleLimit>0)throw Error('请填写视频标题（或先导入素材走统一链路）');
    if(!desc && cfg.titleLimit===0)throw Error('请填写正文（本平台没有独立标题，内容写在正文里）');
    if(title.length>cfg.titleLimit&&cfg.titleLimit)throw Error('标题有 '+title.length+' 字，超过 '+cfg.titleLimit+' 字上限');
    if(desc.length>cfg.descLimit)throw Error('简介有 '+desc.length+' 字，超过 '+cfg.descLimit+' 字上限');
    const plan=serials.map(serial=>({serial,title,caption:desc,album_index:parseInt(($('pub-album')||{}).value||'0',10)||0}));
    await startTasks(plan,cfg.name+'发布：'+title.slice(0,20),cfg.skill,{id:cfg.entry,name:cfg.entryName});
    pubClearForm();
    return;
  }

  // 抖音/小红书：按素材分配或统一用当前素材
  const jobs=[];
  let usedAssign=false;
  for(const [aid,set] of state.pubAssign){if(set.size){usedAssign=true;for(const sn of set)jobs.push({serial:sn,assetId:aid});}}
  if(!usedAssign){
    if(!state.pubCurrent)throw Error('请先选择或导入素材');
    for(const sn of serials)jobs.push({serial:sn,assetId:state.pubCurrent});
  }
  // 校验每条
  const plan=jobs.map(j=>{
    const c=state.pubCopies.get(j.assetId)||{title:'',description:'',tags:[]};
    const title=(c.title||'').trim(),desc=(c.description||'').trim(),tags=(c.tags||[]).slice(0,cfg.tagLimit||5);
    const label=numberLabel(j.serial);
    if(!title)throw Error(label+'：素材「'+(state.pubAssets.find(a=>a.id===j.assetId)||{}).name+'」还没有标题');
    if(title.length>cfg.titleLimit)throw Error(label+'：标题 '+title.length+' 字，超过 '+cfg.titleLimit+' 字上限');
    if(!desc)throw Error(label+'：素材还没有正文');
    if(desc.length>cfg.descLimit)throw Error(label+'：正文 '+desc.length+' 字，超过 '+cfg.descLimit+' 字上限');
    if(tags.length>(cfg.tagLimit||5))throw Error(label+'：话题超过 '+(cfg.tagLimit||5)+' 个');
    return {serial:j.serial,asset_id:j.assetId,title,caption:desc,tags};
  });
  await startTasks(plan,cfg.name+'发布：'+plan[0].title.slice(0,16),cfg.skill,{id:cfg.entry,name:cfg.entryName});
  pubClearForm();
  openDetailForIds(state.tasks.slice(),cfg.name+'发布');
}
function pubClearForm(){
  // 发布下达后清空填写内容，任务转到执行记录（2026-11-03 调整）
  for(const id of ['pub-title','pub-desc','pub-tags','pub-copy-status']){const el=$(id);if(el){el.value='';el.textContent='';}}
  state.pubCopies.clear();
  state.pubAssign.clear();
  state.pubAssets=[];
  state.pubCurrent='';
  const panel=$('pub-assign-panel');
  if(panel){panel.hidden=true;panel.replaceChildren();}
  const ab=$('pub-assign');if(ab)ab.textContent='按手机分配素材';
  const cc=$('pub-copy-status');if(cc){cc.textContent='';cc.className='exec-copy-status';}
  const st=$('pub-status');if(st){st.textContent='已下发，去执行记录查看进度';st.className='exec-copy-status ok';}
  renderPubAssets();renderPubAssign();
}
// 任务已下发：直接把中间区域切到这条任务的详情
async function openDetailForIds(ids,name){
  const tasks=[];
  for(const id of ids){try{tasks.push(await api('/api/execution/tasks/'+id));}catch(e){}}
  if(!tasks.length)return;
  openDetail({id:'live-'+ids[0],name:name||tasks[0].goal||'任务',
              created:tasks[0].created_at||Date.now()/1000,tasks});
}
async function pubStop(){
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的发布任务','err');return;}
  for(const id of ids){try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}}
  ok('已请求停止；已经点出去的发布动作不能撤回');
}

/* ---------- 截流：平台切换与设备 ---------- */
const LEADX_PLATFORMS={
  douyin:{name:'抖音',skill:LEAD_SKILL,entryName:'抖音截流',pool:'douyin',sorts:['综合排序','最新发布','最多点赞','最多评论','最多收藏']},
  xiaohongshu:{name:'小红书',skill:XHS_LEAD_SKILL,entryName:'小红书截流',pool:'redbook',sorts:['综合','最新','最热']},
  bilibili:{name:'B站',skill:BILI_LEAD_SKILL,entryName:'B站截流',pool:'bilibili',sorts:['默认排序','新发布','播放多','弹幕多']},
  tiktok:{name:'TikTok',skill:'builtin:tiktok-lead',entryName:'TikTok截流',pool:'tiktok',sorts:['默认排序','最新发布']},
  facebook:{name:'Facebook',skill:'builtin:facebook-lead',entryName:'Facebook采集',pool:'facebook',sorts:['默认排序','最新贴文']}
};
function leadxCfg(){return LEADX_PLATFORMS[state.leadxPlatform]||LEADX_PLATFORMS.douyin;}
function renderLeadxPlatform(){
  document.querySelectorAll('#lead-platform-tabs button').forEach(b=>b.classList.toggle('on',b.dataset.platform===state.leadxPlatform));
  const cfg=leadxCfg();
  const hint=$('leadx-hint');
  if(hint)hint.textContent='跑到累计命中 '+($('leadx-target').value||'不限')+' 条线索就停（'+cfg.name+'）';
  const sel=$('leadx-sort');
  if(sel){
    const cur=sel.value;
    sel.replaceChildren();
    for(const s of (cfg.sorts||['默认排序'])){
      const o=document.createElement('option');o.value=s;o.textContent=s;sel.append(o);
    }
    if((cfg.sorts||[]).includes(cur))sel.value=cur;
  }
  renderLeadxDevices();
}
function renderLeadxDevices(){
  const paint=(box,set)=>{
    if(!box)return;
    box.replaceChildren();
    for(const d of state.devices){
      const b=document.createElement('button');b.type='button';
      b.className='exec-phone-number'+(set.has(d.serial)?' on':'');
      b.textContent=String(d.index);
      b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
      b.disabled=d.state!=='online';
      b.onclick=()=>{if(set.has(d.serial))set.delete(d.serial);else set.add(d.serial);renderLeadxDevices();};
      box.append(b);
    }
  };
  paint($('leadx-collect-devices'),state.leadxCollect);
  paint($('leadx-reply-devices'),state.leadxReply);
  // 标题上标出已选台数：避免"勾了采集却以为在回复"
  const cl=$('leadx-collect-label'),rl=$('leadx-reply-label');
  if(cl)cl.textContent='采集用手机（已选 '+state.leadxCollect.size+' 台）';
  if(rl)rl.textContent='回复用手机（已选 '+state.leadxReply.size+' 台）';
}
function openLeadPane(){
  state.quickView='leadx';quickRecordId=null;
  setMode('quick');
  const box=$('leadx-results');
  if(box&&!box.children.length)box.append(leadHint('还没有线索。填关键词和采集条数后点「开始采集」；采集不需要填回复内容。'));
  renderLeadxPlatform();
  paintFeedback();
}
/* ---------- 回复（独立面板；2026-11-06 从「截流」拆出来） ----------
   主人反馈：FB/TK 采集完了却没有回复入口。根因是回复按钮一直嵌在截流面板里，
   而且 claim 是从整个池子盲领，没法"只回某一批"。这里做成独立面板：
   选平台 → 刷新批次（按采集日期+关键词）→ 选一批 → 选手机 → 开始回复。 */
const REPLY_PLATFORMS={
  douyin:{name:'抖音',skill:'builtin:douyin-dm',skillName:'抖音私信'},
  xiaohongshu:{name:'小红书',skill:'builtin:xiaohongshu-dm',skillName:'小红书私信'},
  bilibili:{name:'B站',skill:'builtin:bilibili-dm',skillName:'B站私信'},
  tiktok:{name:'TikTok',skill:'builtin:tiktok-dm',skillName:'TikTok私信'},
  facebook:{name:'Facebook',skill:'builtin:facebook-dm',skillName:'Facebook私信'}
};
const replyState={platform:'douyin',batches:[],pick:null,devices:new Set()};

function openReplyPane(){
  state.quickView='replypane';quickRecordId=null;
  setMode('quick');
  renderReplyPlatform();
  renderReplyBatches();
  renderReplyDevices();
  loadReplyBatches();
  paintFeedback();
}

function renderReplyPlatform(){
  document.querySelectorAll('#reply-platform-tabs button').forEach(
    b=>b.classList.toggle('on',b.dataset.platform===replyState.platform));
}

function renderReplyDevices(){
  const box=$('reply-devices');if(!box)return;
  box.replaceChildren();
  for(const d of state.devices){
    const b=document.createElement('button');b.type='button';
    b.className='exec-phone-number'+(replyState.devices.has(d.serial)?' on':'');
    b.textContent=String(d.index);
    b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
    b.disabled=d.state!=='online';
    b.onclick=()=>{
      if(replyState.devices.has(d.serial))replyState.devices.delete(d.serial);
      else replyState.devices.add(d.serial);
      renderReplyDevices();
    };
    box.append(b);
  }
}

async function loadReplyBatches(){
  const st=$('reply-batch-status');
  if(st){st.textContent='正在读取批次…';st.className='exec-copy-status';}
  try{
    const days=Math.max(1,Math.min(365,parseInt(($('reply-days')||{}).value||'60',10)||60));
    const r=await api('/api/lead-pool/batches?platform='+encodeURIComponent(replyState.platform)+'&days='+days);
    replyState.batches=r.batches||[];
    replyState.pick=null;
    renderReplyBatches();
    const withPending=replyState.batches.filter(b=>b.pending>0).length;
    if(st){st.textContent='共 '+replyState.batches.length+' 个批次，其中 '+withPending+' 个有待回线索';st.className='exec-copy-status ok';}
  }catch(e){
    if(st){st.textContent='读取失败：'+e.message;st.className='exec-copy-status err';}
  }
}

function renderReplyBatches(){
  const box=$('reply-batches');if(!box)return;
  box.replaceChildren();
  if(!replyState.batches.length){box.append(leadHint('没有批次。换个平台或放宽天数后再点「刷新批次」。'));return;}
  for(const b of replyState.batches){
    const row=document.createElement('button');
    row.type='button';
    const picked=replyState.pick&&replyState.pick.day===b.day&&replyState.pick.keyword===b.keyword;
    row.className='exec-quick-item'+(picked?' on':'');
    // 2026-11-06 修：批次列表要"两行、够高、超长省略"。
    // 上一版加了 nowrap 但没给 flex 子项 min-width:0，导致行高被挤塌、
    // 文字重叠看不清（主人截图）。这里按 flex 规范补齐。
    row.style.cssText='text-align:left;width:100%;box-sizing:border-box;'
      +'min-height:46px;overflow:hidden;';
    const t=document.createElement('strong');
    t.textContent=b.day+'  ·  '+(b.keyword||'(无关键词)');
    t.style.cssText='display:block;min-width:0;line-height:1.45;overflow:hidden;'
      +'text-overflow:ellipsis;white-space:nowrap;';
    const s=document.createElement('small');
    s.textContent='共 '+b.total+' 条 · 待回 '+b.pending+' · 已草稿 '+b.drafted
      +' · 失败 '+b.failed+(b.skipped?' · 跳过 '+b.skipped:'');
    s.style.cssText='display:block;min-width:0;line-height:1.45;overflow:hidden;'
      +'text-overflow:ellipsis;white-space:nowrap;';
    row.append(t,s);
    row.disabled=!b.pending;
    if(!b.pending)row.title='这一批已经没有待回线索了';
    row.onclick=()=>{replyState.pick={day:b.day,keyword:b.keyword};renderReplyBatches();};
    box.append(row);
  }
}

async function startReply(){
  const st=$('reply-status');
  const bad=(m)=>{if(st){st.textContent=m;st.className='exec-copy-status err';}feedback(m,'err','quick');};
  if(!replyState.pick)return bad('请先选一个采集批次（列表里点一行）');
  if(!replyState.devices.size)return bad('请先勾选「回复用手机」');
  const dm=(($('reply-dm')||{}).value||'').trim();
  if(!dm)return bad('请填写「私信内容」（只填进输入框，不发送）');
  const limit=Math.max(1,Math.min(200,parseInt(($('reply-limit')||{}).value||'10',10)||10));
  const interval=Math.max(0,Math.min(600,parseInt(($('reply-interval')||{}).value||'0',10)||0));
  const cfg=REPLY_PLATFORMS[replyState.platform]||REPLY_PLATFORMS.douyin;
  const serials=[...replyState.devices];
  const batch={day:replyState.pick.day,keyword:replyState.pick.keyword};
  const plan=serials.map(serial=>({serial}));
  if(st){st.textContent='正在下发…';st.className='exec-copy-status';}
  try{
    await startTasks(plan,
      cfg.name+'回复（'+batch.day+' '+(batch.keyword||'')+'）：'+limit+' 条',
      cfg.skill,{id:cfg.skill,name:cfg.skillName},
      {lead:{dm_template:dm,reply_limit:limit,interval_seconds:interval,batch}});
    if(st){
      st.textContent='已下发 '+serials.length+' 台（批次 '+batch.day+' '+(batch.keyword||'')+'），只填草稿';
      st.className='exec-copy-status ok';
    }
    openDetailForIds(state.tasks.slice(),cfg.name+'回复（填草稿）');
  }catch(e){
    if(st){st.textContent='下发失败：'+e.message;st.className='exec-copy-status err';}
  }
}

/* ---------- 手机一键：上方选手机，下方按分组执行 ---------- */
const ONEKEY_GROUPS={network:'网络',display:'显示',sound:'声音',power:'电源',time:'时间',privacy:'隐私',ui:'界面',app:'应用'};
let onekeyActions=[],onekeySelected=new Set();
function openOnekey(){
  state.quickView='onekey';quickRecordId=null;
  setMode('quick');
  renderOnekeyDevices();
  loadOnekeyActions();
  paintFeedback();
  // 2026-11-07 修 BUG（点开大屏后群控失效）：
  //   如果主界面已经拉起了大屏，把它搬进工作台里，
  //   否则那个 z-index:80 的浮层会压在面板上，按钮点不到。
  try {
    const cur = (window.S && (window.S.expanded || window.S.selected)) || window.__ctxSerial;
    const stageEl = document.getElementById('stage');
    const visible = stageEl && !stageEl.classList.contains('hidden');
    if (cur && visible && typeof window.openAiBigscreen === 'function') {
      window.openAiBigscreen(cur).catch(() => {});
    }
  } catch (e) {}
}
function renderOnekeyDevices(){
  const box=$('onekey-devices');if(!box)return;
  box.replaceChildren();
  for(const d of state.devices){
    const b=document.createElement('button');b.type='button';
    b.className='exec-phone-number'+(onekeySelected.has(d.serial)?' on':'');
    b.textContent=String(d.index);
    b.title=d.index+'号 · '+(d.name||d.model||'')+' · '+d.serial;
    b.disabled=d.state!=='online';
    b.onclick=()=>{if(onekeySelected.has(d.serial))onekeySelected.delete(d.serial);else onekeySelected.add(d.serial);renderOnekeyDevices();};
    box.append(b);
  }
}
async function loadOnekeyActions(){
  const st=$('onekey-status');
  try{
    if(st){st.textContent='正在读取预制动作…';st.className='exec-copy-status';}
    const v=await api('/api/devices/onekey/actions');
    onekeyActions=v.actions||[];
    renderOnekeyGroups();
    if(st){st.textContent='已载入 '+onekeyActions.length+' 个预制动作';st.className='exec-copy-status ok';}
  }catch(e){if(st){st.textContent='读取失败：'+e.message;st.className='exec-copy-status err';}}
}
function renderOnekeyGroups(){
  const box=$('onekey-groups');if(!box)return;
  box.replaceChildren();
  const q=(($('onekey-search')||{}).value||'').trim().toLowerCase();
  const byGroup={};
  for(const a of onekeyActions){
    if(q&&!(a.name+a.key).toLowerCase().includes(q))continue;
    const gk=a.group||'';
    (byGroup[gk]=byGroup[gk]||[]).push(a);
  }
  for(const [g,items] of Object.entries(byGroup)){
    const sec=document.createElement('div');
    const h=document.createElement('div');h.className='exec-lead-devices-label';h.textContent=ONEKEY_GROUPS[g]||g||'其他';
    sec.append(h);
    const grid=document.createElement('div');grid.className='exec-phone-grid';
    for(const a of items){
      const b=document.createElement('button');b.type='button';
      b.textContent=a.name;b.title=(a.note||a.key)+(a.risk==='danger'?'（高危，需二次确认）':(a.risk==='high'?'（高风险，需确认）':''));
      b.className='exec-phone-number'+((a.risk==='danger')?' exec-danger':(a.risk==='high'?' exec-warn':''));
      if(a.risk==='danger')b.style.cssText='border-color:#a33;color:#f88';
      b.onclick=()=>runOnekey(a);
      grid.append(b);
    }
    sec.append(grid);box.append(sec);
  }
  if(!box.children.length)box.append(leadHint('没有匹配的动作，换个关键词试试。'));
}
async function runOnekey(a){
  const st=$('onekey-status'),logBox=$('onekey-results');
  if(!onekeySelected.size){feedback('请先在上方勾选手机','err','quick');return;}
  let param='',confirm=false;
  if(/<.+>/.test(a.key)||/<.+>/.test(a.note||'')){}
  // 需要参数的动作（亮度/音量/包名等）弹窗输入
  try{
    const v=await api('/api/devices/onekey/actions');
    const full=(v.actions||[]).find(x=>x.key===a.key);
  }catch(e){}
  if(a.key==='brightness_set')param=prompt('亮度 0-255（建议 100-150）','120')||'';
  if(a.key==='volume_set')param=prompt('媒体音量 0-15','6')||'';
  if(a.key==='screen_timeout_set')param=prompt('息屏毫秒（30000/60000/2147483647=永不）','2147483647')||'';
  if(a.key==='kill_app'||a.key==='clear_app_data'||a.key==='disable_app'||a.key==='enable_app'||a.key==='uninstall_app')param=prompt('应用包名（如 tv.danmaku.bili）','')||'';
  if((a.risk==='danger'||a.risk==='high')&&!confirm){
    if(!window.confirm('确认在 '+onekeySelected.size+' 台手机执行「'+a.name+'」？'+(a.note?'\n'+a.note:'')))return;
    confirm=true;
  }
  if(st){st.textContent='正在执行 '+a.name+'（'+onekeySelected.size+' 台）…';st.className='exec-copy-status';}
  try{
    const r=await api('/api/devices/onekey/run',{serials:[...onekeySelected],key:a.key,param,confirm});
    const okN=Object.values(r.results||{}).filter(x=>x.ok).length;
    if(st){st.textContent=a.name+' 完成：'+okN+'/'+[...onekeySelected].size+' 台成功';st.className='exec-copy-status ok';}
    if(logBox){
      const d=document.createElement('div');d.className='exec-detail-event';
      d.textContent=new Date().toLocaleTimeString()+' '+a.name+'：'+okN+'/'+[...onekeySelected].size+' 成功';
      logBox.prepend(d);
    }
  }catch(e){if(st){st.textContent='执行失败：'+e.message;st.className='exec-copy-status err';}}
}

/* ---------- 一键自检：核对真实坐标，按机型修复 ---------- */
let __onboardTimer=null,__onboardId=null,__onboardOid='';
function renderOnboard(rep){
  const box=$('onekey-onboard-out');if(!box)return;
  box.style.display='';
  box.replaceChildren();
  const card=document.createElement('div');
  card.style.cssText='border:1px solid #2a2f3a;border-radius:8px;padding:8px 10px;background:#0e1218';
  const head=document.createElement('div');
  head.style.cssText='display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:6px';
  const vd=rep.verdict||'running';
  const vcol=vd==='pass'?'#6c8':(vd==='needs_review'?'#f0b060':'#f08080');
  head.innerHTML='<b>新机模拟测试</b>'
    +'<span style="color:#8ab">'+esc(rep.serial||'')+'</span>'
    +'<span style="color:'+vcol+'">'+esc(vd)+'</span>'
    +(rep.report_path?'<span style="color:#789">'+esc(rep.report_path.split(/[\\/]/).pop())+'</span>':'');
  card.append(head);
  const scr=rep.screen||{};
  if(scr.width){
    const g=document.createElement('div');g.className='exec-detail-event';
    g.style.cssText='font-size:12px;color:#9ab';
    g.textContent='几何 '+scr.width+'×'+scr.height+'@'+scr.dpi+' → dp '+[scr.dp_w,scr.dp_h].join('×')+'（'+scr.dp_family+'）';
    card.append(g);
  }
  for(const s of rep.sims||[]){
    const row=document.createElement('div');row.className='exec-detail-event';
    row.style.cssText='font-size:12px;display:flex;gap:8px;flex-wrap:wrap';
    const col=s.status==='pass'?'#6c8':'#f08080';
    row.innerHTML='<span style="color:'+col+';min-width:58px">'+esc(s.status)+'</span>'
      +'<span style="color:#bcd">'+esc(s.platform+'·'+s.kind)+'</span>'
      +'<span style="color:#777">'+esc(s.reason||s.detail||'')+'</span>';
    card.append(row);
  }
  for(const x of rep.repairs||[]){
    const row=document.createElement('div');row.className='exec-detail-event';
    row.style.cssText='font-size:12px;color:#5cf';
    row.textContent='已修正 '+x.key+' → dp '+JSON.stringify(x.dp)+'（漂移'+x.drift+' 置信度'+x.confidence+'）';
    card.append(row);
  }
  for(const x of rep.human||[]){
    const row=document.createElement('div');row.className='exec-detail-event';
    row.style.cssText='font-size:12px;color:#f0b060';
    row.textContent='需人工 '+(x.key||'')+'：'+x.reason;
    card.append(row);
  }
  box.append(card);
  const pend=rep.pending_promotion||{};
  const pkeys=Object.keys(pend);
  const promoted=rep.promoted||'';
  if(pkeys.length&&!promoted){
    const p=document.createElement('div');
    p.style.cssText='margin-top:8px;padding:8px 10px;border:1px solid #4a3a1a;border-radius:8px;background:#1a1608';
    const t=document.createElement('div');
    t.style.cssText='color:#f0b060;font-size:12px;margin-bottom:6px';
    t.textContent='本次自愈修正了 '+pkeys.length+' 处坐标（'+pkeys.join('、')+'）。是否把该设置覆盖到剩余手机？';
    const yes=document.createElement('button');
    yes.type='button';yes.textContent='覆盖到所有手机';yes.style.marginRight='8px';
    const no=document.createElement('button');
    no.type='button';no.textContent='仅保留本机（其它手机用默认）';
    const tip=document.createElement('div');
    tip.style.cssText='font-size:12px;color:#9ab;margin-top:6px';
    const doPromote=async(scope)=>{
      const oid=__onboardOid||__onboardId||'';
      if(!oid){tip.textContent='找不到任务ID，请重新跑一次';return;}
      yes.disabled=true;no.disabled=true;
      try{
        const r=await api('/api/devices/onboard/'+oid+'/promote',{scope});
        if(!r.ok)throw Error(r.error||'操作失败');
        tip.textContent=scope==='all'
          ?('已覆盖到所有手机（'+r.count+' 项，后续新机默认吃这套）')
          :'已设为仅本机生效，其它手机继续用默认坐标';
        tip.style.color='#6c8';
      }catch(e){tip.textContent='操作失败：'+e.message;yes.disabled=false;no.disabled=false;}
    };
    yes.onclick=()=>doPromote('all');
    no.onclick=()=>doPromote('this');
    p.append(t,yes,no,tip);
    box.append(p);
  }
}
async function pollOnboard(){
  if(!__onboardId)return;
  try{
    const r=await api('/api/devices/onboard/'+__onboardId);
    const st=$('onekey-status');
    if(!r.ok){if(st){st.textContent='模拟测试失败：'+(r.error||'');st.className='exec-copy-status err';}__onboardId=null;return;}
    if(st){st.textContent='模拟测试进行中…（日志见下方可随时停止）';st.className='exec-copy-status';}
    if(r.report){__onboardOid=r.onboard_id||__onboardId;renderOnboard(r.report);}
    else{
      const box=$('onekey-onboard-out');if(box){box.style.display='';
        const lines=(r.log||[]).slice(-12);
        box.innerHTML='<div style="border:1px solid #2a2f3a;border-radius:8px;padding:8px 10px;background:#0e1218;font-size:12px;color:#9ab">'
          +'<div style="margin-bottom:4px;color:#bcd">进行中 '+esc(r.serial||'')+' <button id="onekey-onboard-stop" type="button">停止</button></div>'
          +lines.map(l=>'<div>'+esc(l)+'</div>').join('')+'</div>';
        const stopBtn=$('onekey-onboard-stop');
        if(stopBtn)stopBtn.onclick=()=>stopOnboard();
      }
    }
    if(r.status==='running'){__onboardTimer=setTimeout(pollOnboard,4000);return;}
    if(st){const v=(r.report||{}).verdict||r.status;
      st.textContent='模拟测试结束：'+v;st.className='exec-copy-status '+(v==='pass'?'ok':'err');}
    __onboardId=null;
  }catch(e){const st=$('onekey-status');if(st){st.textContent='读取进度失败：'+e.message;st.className='exec-copy-status err';}__onboardTimer=setTimeout(pollOnboard,6000);}
}
async function stopOnboard(){
  if(!__onboardId)return;
  try{await api('/api/devices/onboard/'+__onboardId+'/stop',{});}catch(e){}
  clearTimeout(__onboardTimer);__onboardId=null;
  const st=$('onekey-status');if(st){st.textContent='已请求停止';st.className='exec-copy-status';}
}
async function runOnboard(){
  if(onekeySelected.size!==1){feedback('新机模拟测试一次只测一台请只勾一台手机','err','quick');return;}
  const serial=[...onekeySelected][0];
  const btn=$('onekey-onboard');
  if(btn)btn.disabled=true;
  try{
    const r=await api('/api/devices/'+encodeURIComponent(serial)+'/onboard',{});
    if(!r.ok)throw Error(r.error||'启动失败');
    __onboardId=r.onboard_id;
    const st=$('onekey-status');
    if(st){st.textContent='已启动新机模拟测试（几何→自检→发布/采集模拟→视觉修正）…';st.className='exec-copy-status';}
    pollOnboard();
  }catch(e){feedback('启动失败：'+e.message,'err','quick');}
  finally{if(btn)btn.disabled=false;}
}
const SELFCHECK_LABELS={ok:'正常',recalibrated:'已校正',miss:'坐标偏了',skipped:'未切到该应用',need_nav:'需回首页'};
function renderSelfcheck(results){
  const box=$('onekey-selfcheck-out');if(!box)return;
  box.style.display='';
  box.replaceChildren();
  for(const rep of results||[]){
    const card=document.createElement('div');
    card.style.cssText='border:1px solid #2a2f3a;border-radius:8px;padding:8px 10px;margin-bottom:8px;background:#0e1218';
    if(!rep.ok){
      card.innerHTML='<div style="color:#f08080">'+esc((rep.serial||'')+' 自检失败：'+(rep.error||''))+'</div>';
      box.append(card);continue;
    }
    const s=rep.summary||{},g=rep.screen||{};
    const head=document.createElement('div');
    head.style.cssText='display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:6px';
    head.innerHTML='<b>'+esc(rep.serial)+'</b>'
      +'<span style="color:#8ab">'+esc(g.width+'×'+g.height+' @'+g.dpi+'dpi')+'</span>'
      +'<span style="color:#9ab">'+esc(g.dp_w+'×'+g.dp_h+'dp')+'</span>'
      +'<span style="color:#789">dp系数'+esc(g.px_per_dp)+'</span>'
      +'<span style="color:#9ab">状态栏'+g.status_bar+' 导航栏'+g.nav_bar+'</span>'
      +(rep.dp_changed?'<span style="color:#f08080">宽度dp与基准不同，必须自检</span>':'')
      +((g.density_override||g.size_override)?'<span style="color:#f0b060">手动改过分辨率/密度</span>':'')
      +(rep.size_changed?'<span style="color:#f0b060">分辨率与基准不同</span>':'')
      +(rep.dpi_changed?'<span style="color:#f0b060">DPI与基准不同</span>':'')
      +'<span style="color:#6c8">正常'+s.ok+'</span>'
      +'<span style="color:#5cf">校正'+s.recalibrated+'</span>'
      +((s.miss?'<span style="color:#f08080">偏移'+s.miss+'</span>':''))
      +((s.need_nav?'<span style="color:#f0b060">需回首页'+s.need_nav+'</span>':''))
      +((s.skipped?'<span style="color:#777">未测'+s.skipped+'</span>':''));
    card.append(head);
    for(const it of rep.items||[]){
      if(it.result==='skipped')continue;
      const row=document.createElement('div');
      row.className='exec-detail-event';
      row.style.cssText='font-size:12px;display:flex;gap:8px;flex-wrap:wrap';
      const col=it.result==='ok'?'#6c8':(it.result==='recalibrated'?'#5cf':'#f08080');
      const off=it.offset?('偏 ('+it.offset[0]+','+it.offset[1]+')'):'';
      row.innerHTML='<span style="color:'+col+';min-width:58px">'+esc(SELFCHECK_LABELS[it.result]||it.result)+'</span>'
        +'<span style="color:#bcd">'+esc(it.key)+'</span>'
        +'<span style="color:#789">期望'+esc((it.expect_point||[]).join(','))+'</span>'
        +(it.actual_point?'<span style="color:#789">实测'+esc(it.actual_point.join(','))+' '+esc(off)+'</span>':'')
        +(it.dp_actual?'<span style="color:#6c8">'+esc(it.anchor)+' '+esc(it.dp_actual[0]+'dp,'+it.dp_actual[1]+'dp')+'</span>':'')
        +(it.matched_text?'<span style="color:#9ab">「'+esc(it.matched_text)+'」</span>':'')
        +((it.reason)?'<span style="color:#777">'+esc(it.reason)+'</span>':'');
      card.append(row);
    }
    if(rep.profile_path){
      const p=document.createElement('div');p.className='exec-detail-event';
      p.style.cssText='font-size:11px;color:#6c8;margin-top:4px';
      p.textContent='已写入机型档案：'+rep.device_key;
      card.append(p);
    }
    box.append(card);
  }
}
async function runSelfcheck(){
  const st=$('onekey-status'),btn=$('onekey-selfcheck');
  if(!onekeySelected.size){feedback('请先在上方勾选手机','err','quick');return;}
  const navigate=($('onekey-selfcheck-nav')||{}).checked!==false;
  const applyFix=($('onekey-selfcheck-fix')||{}).checked!==false;
  const allApps=($('onekey-selfcheck-all')||{}).checked===true;
  const hardNav=($('onekey-selfcheck-hard')||{}).checked===true;
  if(btn)btn.disabled=true;
  if(st){st.textContent='正在自检 '+onekeySelected.size+' 台（逐台读屏幕、比对坐标）…';st.className='exec-copy-status';}
  try{
    const r=await api('/api/devices/selfcheck',{serials:[...onekeySelected],navigate,apply_fix:applyFix,all_apps:allApps,hard_nav:hardNav});
    renderSelfcheck(r.results||[]);
    const done=(r.results||[]).filter(x=>x.ok);
    const fixed=done.reduce((n,x)=>n+((x.summary||{}).recalibrated||0),0);
    const bad=done.reduce((n,x)=>n+((x.summary||{}).miss||0),0);
    if(st){
      st.textContent='自检完成：'+done.length+'/'+onekeySelected.size+' 台，校正 '+fixed+' 项，仍偏 '+bad+' 项';
      st.className='exec-copy-status '+(bad?'err':'ok');
    }
  }catch(e){
    if(st){st.textContent='自检失败：'+e.message;st.className='exec-copy-status err';}
  }finally{if(btn)btn.disabled=false;}
}
function leadxConfig(){
  const keywords=String($('leadx-keywords').value||'').split(/\r?\n/).map(s=>s.trim()).filter(Boolean).slice(0,20);
  if(!keywords.length)throw Error('请至少填一个关键词');
  const target=Math.max(0,Math.min(20000,parseInt($('leadx-target').value||'0',10)||0));
  return {
    keywords,
    target_comments:target,
    sort_by:($('leadx-sort')||{}).value||'',
    max_age_days:Math.max(0,Math.min(365,parseInt(($('leadx-days')||{}).value||'0',10)||0)),
    task_created_at:Math.floor(Date.now()/1000),
    intent_words:splitWords($('leadx-intent').value).slice(0,50),
    exclude_words:splitWords($('leadx-exclude').value).slice(0,50),
    dm_template:$('leadx-dm').value.trim(),
    // 采集不做冷却（只读，不触发风控）；冷却只保留给「回复」。
    // 注意 cooldown_every 后端上限 500，不能拿超大值来"关掉"，
    // 关闭靠 cooldown_seconds=0（技能里 0 直接 return）。
    cooldown_every:10,
    cooldown_seconds:0
  };
}
async function runLeadxCollect(){
  if(!state.leadxCollect.size)throw Error('请先勾选「采集用手机」');
  const cfg=leadxCfg();
  const lead=leadxConfig();
  lead.collect_serials=[...state.leadxCollect];
  $('leadx-status').textContent='正在下发…';$('leadx-status').className='exec-copy-status';
  const plan=[...state.leadxCollect].map(serial=>({serial}));
  try{
    await startTasks(plan,cfg.name+'截流：'+lead.keywords.slice(0,3).join('、'),
                     cfg.skill,{id:cfg.skill,name:cfg.entryName},{lead});
    $('leadx-status').textContent='已下发，采集中';
    $('leadx-status').className='exec-copy-status ok';
    const box=$('leadx-results');if(box)box.replaceChildren(leadHint('采集中：会自动搜索、读评论并写进中转站；结束后去执行记录查看。'));
    openDetailForIds(state.tasks.slice(),cfg.name+'截流：'+lead.keywords.slice(0,3).join('、'));
  }finally{
    if($('leadx-status').textContent==='正在下发…')$('leadx-status').textContent='';
  }
}
let leadxReplyRunning=false;
async function runLeadxReply(){
  const st=$('leadx-status');
  const bad=(msg)=>{if(st){st.textContent=msg;st.className='exec-copy-status err';}feedback(msg,'err','quick');return false;};
  // 提示必须写在按钮旁边，不能只写页面底部（2026-11-03 反馈：点了没反应）
  if(!state.leadxReply.size)return bad('请先在「回复用手机」里勾选手机（右边那组，不是采集用手机）');
  const dm=($('leadx-dm')&&$('leadx-dm').value||'').trim();
  if(!dm)return bad('请先填写「私信内容」（回复时必填，只填进输入框不发送）');
  const limit=Math.max(1,Math.min(200,parseInt($('leadx-reply-limit').value||'20',10)||20));
  const interval=Math.max(0,Math.min(600,parseInt($('leadx-interval').value||'0',10)||0));
  const serials=[...state.leadxReply];
  const plat=state.leadxPlatform;
  const logBox=$('leadx-results');
  const logLine=(text)=>{
    if(!logBox)return;
    if(logBox.firstChild&&logBox.firstChild.className==='exec-side-note')logBox.replaceChildren();
    const d=document.createElement('div');d.className='exec-detail-event';d.textContent=text;
    logBox.append(d);logBox.scrollTop=logBox.scrollHeight;
  };

  if(plat==='douyin'){
    // 抖音回复也做成任务：执行记录里能看到状态和日志（2026-11-03 主人要求）
    st.textContent='正在下发抖音回复任务…';st.className='exec-copy-status';
    const plan=serials.map(serial=>({serial}));
    await startTasks(plan,leadxCfg().name+'回复（填草稿）：'+limit+' 条',
                     'builtin:douyin-dm',{id:'builtin:douyin-dm',name:'抖音私信'},
                     {lead:{dm_template:dm,reply_limit:limit,interval_seconds:interval}});
    st.textContent='已下发 '+serials.length+' 台，正在填草稿（不会发送）';st.className='exec-copy-status ok';
    logLine('已下发抖音回复：'+serials.map(numberLabel).join('、')+'，上限 '+limit+' 条');
    openDetailForIds(state.tasks.slice(),'抖音私信（填草稿）');
    return;
  }

  // 小红书 / B站 / TikTok 回复：下发任务（技能内部逐条填草稿，不发送）
  const skill=plat==='xiaohongshu'?XHS_DM_SKILL:(plat==='tiktok'?'builtin:tiktok-dm':(plat==='facebook'?'builtin:facebook-dm':'builtin:bilibili-dm'));
  const name=plat==='xiaohongshu'?'小红书私信':(plat==='tiktok'?'TikTok私信':(plat==='facebook'?'Facebook私信':'B站私信'));
  st.textContent='正在下发回复任务…';st.className='exec-copy-status';
  const plan=serials.map(serial=>({serial}));
  try{
    await startTasks(plan,leadxCfg().name+'回复（填草稿）：'+limit+' 条',skill,{id:skill,name},
                     {lead:{dm_template:dm,reply_limit:limit,interval_seconds:interval}});
    st.textContent='已下发 '+serials.length+' 台，正在填草稿（不会发送）';st.className='exec-copy-status ok';
    logLine('已下发回复任务：'+serials.map(numberLabel).join('、')+'，上限 '+limit+' 条');
    openDetailForIds(state.tasks.slice(),name+'（填草稿）');
  }finally{
    if(st.textContent==='正在下发回复任务…')st.textContent='';
  }
}
async function leadxStop(){
  leadxReplyRunning=false;
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的截流任务','err');return;}
  for(const id of ids){try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}}
  $('leadx-status').textContent='已请求停止';
  ok('已请求停止；已发出的动作不能撤回');
}

/* ---------- 新版绑定 ---------- */
document.querySelectorAll('#pub-platform-tabs button').forEach(b=>{
  b.onclick=()=>{pubFlushCopy();state.pubPlatform=b.dataset.platform;state.pubCurrent=state.pubCurrent||'';renderPubPlatform();pubLoadCopy();renderPubAssign();};
});
document.querySelectorAll('#lead-platform-tabs button').forEach(b=>{
  b.onclick=()=>{state.leadxPlatform=b.dataset.platform;renderLeadxPlatform();};
});
/* ---------- 回复面板（独立）绑定 ---------- */
document.querySelectorAll('#reply-platform-tabs button').forEach(b=>{
  b.onclick=()=>{
    replyState.platform=b.dataset.platform;
    renderReplyPlatform();
    replyState.pick=null;
    renderReplyBatches();
    loadReplyBatches();
  };
});
if($('reply-refresh'))$('reply-refresh').onclick=()=>loadReplyBatches();
if($('reply-days'))$('reply-days').onchange=()=>loadReplyBatches();
if($('reply-run'))$('reply-run').onclick=async()=>{
  try{await startReply();}catch(e){feedback('下发失败：'+e.message,'err','quick');}
};
if($('reply-stop'))$('reply-stop').onclick=async()=>{
  const ids=state.tasks.slice();
  if(!ids.length){feedback('当前没有正在跑的回复任务','err');return;}
  for(const id of ids){try{await api('/api/execution/tasks/'+id+'/stop',{});}catch(e){}}
  const st=$('reply-status');
  if(st){st.textContent='已请求停止';st.className='exec-copy-status';}
  ok('已请求停止；已发出的动作不能撤回');
};
if($('pub-upload'))$('pub-upload').onclick=()=>$('pub-file').click();
if($('pub-upload-dir'))$('pub-upload-dir').onclick=()=>$('pub-file-dir').click();
if($('pub-library-add'))$('pub-library-add').onclick=()=>pubLibraryAdd();
if($('pub-file'))$('pub-file').onchange=async()=>{
  const files=[...$('pub-file').files];if(!files.length)return;
  await pubAddAssets(files);$('pub-file').value='';
};
if($('pub-file-dir'))$('pub-file-dir').onchange=async()=>{
  // 选文件夹：只收视频/图片，忽略其它文件
  const files=[...$('pub-file-dir').files].filter(f=>/\.(mp4|mov|mkv|webm|jpe?g|png|webp)$/i.test(f.name));
  if(!files.length){feedback('文件夹里没有可用的视频或图片','err');$('pub-file-dir').value='';return;}
  await pubAddAssets(files);$('pub-file-dir').value='';
};
if($('pub-copy'))$('pub-copy').onclick=()=>pubGenerateCopy();
if($('pub-assign'))$('pub-assign').onclick=()=>{
  const panel=$('pub-assign-panel');
  panel.hidden=!panel.hidden;
  $('pub-assign').textContent=panel.hidden?'按手机分配素材':'收起分配';
  renderPubAssign();
};
if($('pub-run'))$('pub-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runPublish();}catch(e){feedback(e.message,'err');}
  finally{state.busy=false;}
};
if($('pub-stop'))$('pub-stop').onclick=()=>pubStop();
if($('leadx-run'))$('leadx-run').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runLeadxCollect();}catch(e){feedback(e.message,'err','quick');$('leadx-status').textContent='';}
  finally{state.busy=false;}
};
if($('leadx-reply'))$('leadx-reply').onclick=async()=>{
  if(state.busy)return;state.busy=true;
  try{await runLeadxReply();}catch(e){feedback(e.message,'err','quick');$('leadx-status').textContent='';}
  finally{state.busy=false;}
};
if($('leadx-stop'))$('leadx-stop').onclick=()=>leadxStop();
// 断点续接开关已移到执行记录详情里（按任务单独开关），新建区不再放开关。
async function loadResumeSwitch(){return;}
// 一键把采集用手机复制到回复用手机（常勾错组）
if($('leadx-copy-phones'))$('leadx-copy-phones').onclick=()=>{
  state.leadxReply=new Set(state.leadxCollect);
  renderLeadxDevices();
  feedback('已把采集用手机（'+state.leadxReply.size+' 台）复制到回复用手机','ok','quick');
};
if($('leadx-target'))$('leadx-target').oninput=()=>renderLeadxPlatform();

/* 启动 */
setMode('quick');
renderPubPlatform();renderPubAssets();renderPubAssign();
pubLoadLibrary();
loadResumeSwitch();
renderWrite();
renderThumbs();
if($('onekey-reload'))$('onekey-reload').onclick=()=>loadOnekeyActions();
if($('onekey-search'))$('onekey-search').oninput=()=>renderOnekeyGroups();
if($('onekey-selfcheck'))$('onekey-selfcheck').onclick=()=>runSelfcheck();
if($('onekey-onboard'))$('onekey-onboard').onclick=()=>runOnboard();
refresh().catch(e=>log(e.message));
loadAssets().catch(e=>log(e.message));
})();
