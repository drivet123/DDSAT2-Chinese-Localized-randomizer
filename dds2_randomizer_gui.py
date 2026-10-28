#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Digital Devil Saga 2 randomizer - graphical front end (PySide2).

Every randomizable part has its own switch, so a build can be as small as
"only shuffle the dialogue" or as large as the full treatment.  The settings
map 1:1 onto `ddstool.config.Config`, and can be saved to / loaded from a
JSON file (the same file the command line front end takes with --config).

    python dds2_randomizer_gui.py
"""
import os
import sys
import random
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'src'))

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:                                       # pragma: no cover
    sys.stderr.write(
        'PySide2 is required for the GUI.\n'
        'Install it with:  python -m pip install PySide2\n'
        'or use the command line front end:  python dds2_randomizer.py --help\n')
    raise SystemExit(2)

from ddstool.config import Config
from ddstool.randomizer import Randomizer

VERSION = '1.0'


class Worker(QtCore.QThread):
    message = QtCore.Signal(str)
    finished_ok = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, src, out, cfg):
        QtCore.QThread.__init__(self)
        self.src = src
        self.out = out
        self.cfg = cfg

    def run(self):
        try:
            r = Randomizer(self.src, self.out, self.cfg, log=self.message.emit)
            self.finished_ok.emit(r.run())
        except Exception:
            self.failed.emit(traceback.format_exc())


class Window(QtWidgets.QWidget):
    def __init__(self):
        QtWidgets.QWidget.__init__(self)
        self.setWindowTitle('DDS2 Randomizer  v%s' % VERSION)
        self.worker = None
        # The story-battle shuffle is shielded (see ddstool/config.py), so the
        # GUI has no widgets for it; these stay None for the shared code paths.
        self.bosses = self.boss_mult = None
        self.boss_hp = self.boss_strength = None
        self._build()
        self._sync()

    # ------------------------------------------------------------- layout
    def _build(self):
        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._tab_basic(), '基本')
        tabs.addTab(self._tab_enemy(), '敌人')
        tabs.addTab(self._tab_text(), '文本与语音')
        tabs.addTab(self._tab_adv(), '高级')

        self.start = QtWidgets.QPushButton('开始构建')
        self.start.setMinimumHeight(34)
        self.start.clicked.connect(self._start)
        self.reset = QtWidgets.QPushButton('清理日志')
        self.reset.clicked.connect(lambda: self.log.clear())

        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.start, 3)
        row.addWidget(self.reset, 1)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)          # indeterminate while building
        self.bar.setVisible(False)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(230)
        self.log.setStyleSheet('font-family: Consolas, monospace; font-size: 11px;')

        self.status = QtWidgets.QLabel('就绪')
        self.status.setWordWrap(True)

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(tabs)
        root.addLayout(row)
        root.addWidget(self.bar)
        root.addWidget(self.status)
        root.addWidget(self.log)
        self.resize(820, 700)

    def _tab_basic(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)

        self.iso = QtWidgets.QLineEdit(os.path.join(HERE, 'DDSAGA2.iso'))
        b = QtWidgets.QPushButton('浏览…')
        b.clicked.connect(self._pick_iso)
        r = QtWidgets.QHBoxLayout(); r.addWidget(self.iso); r.addWidget(b)
        f.addRow('原版 ISO', r)

        self.out = QtWidgets.QLineEdit(os.path.join(HERE, 'DDS2_random.iso'))
        b2 = QtWidgets.QPushButton('另存为…')
        b2.clicked.connect(self._pick_out)
        r2 = QtWidgets.QHBoxLayout(); r2.addWidget(self.out); r2.addWidget(b2)
        f.addRow('输出 ISO', r2)

        self.seed = QtWidgets.QLineEdit('')
        self.seed.setPlaceholderText('留空 = 每次随机')
        b3 = QtWidgets.QPushButton('随机')
        b3.clicked.connect(lambda: self.seed.setText(str(random.randrange(1, 2 ** 31))))
        r3 = QtWidgets.QHBoxLayout(); r3.addWidget(self.seed); r3.addWidget(b3)
        f.addRow('随机种子', r3)

        self.backup = QtWidgets.QCheckBox('写入前备份输出文件（如果已存在）')
        self.backup.setChecked(True)
        self.verify = QtWidgets.QCheckBox('构建后校验输出文件')
        self.verify.setChecked(True)
        f.addRow(self.backup)
        f.addRow(self.verify)

        self.load_cfg = QtWidgets.QPushButton('载入配置…')
        self.load_cfg.clicked.connect(self._load_cfg)
        self.save_cfg = QtWidgets.QPushButton('保存配置…')
        self.save_cfg.clicked.connect(self._save_cfg)
        r4 = QtWidgets.QHBoxLayout(); r4.addWidget(self.load_cfg); r4.addWidget(self.save_cfg)
        f.addRow('配置文件 (JSON)', r4)
        return w

    def _tab_enemy(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)

        self.enc = QtWidgets.QCheckBox('随机普通遭遇敌人（1:1 互换外形 / 名字）')
        self.enc.setChecked(True)
        self.skills = QtWidgets.QCheckBox('技能列表跟随被替换掉的敌人')
        self.skills.setChecked(True)
        self.ai = QtWidgets.QCheckBox('敌人 AI 出招跟随新技能组')
        self.ai.setChecked(True)
        self.table = QtWidgets.QCheckBox('生成「敌人 ID ↔ 属性」对照表（输出旁边）')
        self.table.setChecked(True)
        for x in (self.enc, self.skills, self.ai, self.table):
            f.addRow(x)
            x.toggled.connect(self._sync)

        self.variety = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.variety.setRange(0, 100); self.variety.setValue(50)
        self.variety_lbl = QtWidgets.QLabel()
        self.variety.valueChanged.connect(self._sync)
        r = QtWidgets.QHBoxLayout(); r.addWidget(self.variety); r.addWidget(self.variety_lbl)
        f.addRow('遭遇变化幅度', r)

        self.tol = QtWidgets.QDoubleSpinBox()
        self.tol.setRange(0.05, 1.0); self.tol.setSingleStep(0.05); self.tol.setValue(0.25)
        f.addRow('强度容差（越小越贴近原强度）', self.tol)

        self.stats_mode = QtWidgets.QComboBox()
        self.stats_mode.addItems(['copy — 五项能力原样照搬（推荐）',
                                  'scale — 只让总和相等'])
        f.addRow('五项能力对齐方式', self.stats_mode)

        self.ban_file = QtWidgets.QLineEdit('')
        self.ban_file.setPlaceholderText('可选：每行一个敌人 ID 的文件（或 JSON {"ban":[..]}）')
        bb = QtWidgets.QPushButton('浏览…')
        bb.clicked.connect(self._pick_ban)
        rb = QtWidgets.QHBoxLayout(); rb.addWidget(self.ban_file); rb.addWidget(bb)
        f.addRow('禁止出场名单（文件）', rb)

        self.ban_ids = QtWidgets.QLineEdit('')
        self.ban_ids.setPlaceholderText('可选：直接填 ID，逗号分隔，例如 120,121,130')
        f.addRow('禁止出场名单（直接填）', self.ban_ids)

        hint = QtWidgets.QLabel(
            '不知道填什么可以跑 <code>python tools/enemy_info.py --used-only</code> 查看敌人 ID。')
        hint.setWordWrap(True)
        f.addRow(hint)

        box = QtWidgets.QGroupBox('Boss / 精英敌人可被分析 —— 已实机验证')
        bf = QtWidgets.QFormLayout(box)
        self.reveal = QtWidgets.QCheckBox('让 Boss / 精英敌人也能被分析')
        self.reveal.setChecked(False)
        self.reveal.setToolTip('改的是游戏主程序里那十三个「显示成 ？ 」的判断分支，'
                               '不碰任何游戏数据，所以不影响战斗加载。')
        self.reveal.toggled.connect(self._sync)
        bf.addRow(self.reveal)
        rnote = QtWidgets.QLabel(
            '打开后分析 Boss 时会显示原作中被隐藏的内容：<b>等级 / HP / MP </b>。<br>'
            '<b>注: 技能内容仍然是「？」。</b>')
        rnote.setWordWrap(True)
        bf.addRow(rnote)
        f.addRow(box)
        return w

    def _tab_text(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)

        self.text = QtWidgets.QCheckBox('随机 CG 字幕（只做等长互换）')
        self.text.setChecked(True)
        self.text.setToolTip('只有字节长度完全相同的两条字幕才会互换。')
        self.text.toggled.connect(self._sync)
        self.cg_mode = QtWidgets.QComboBox()
        self.cg_mode.addItems(['global — 和任意一段 CG 互换（默认）',
                               'file — 只在同一段 CG 内互换',
                               'off — 完全不动 CG 字幕'])
        self.cg_mode.setToolTip('只换字幕里的纯文字，控制码一个字节都不动 —— 因为 CG 的控制码是'
                                '每段过场专属的（e601 是 f207，e603 是 f41305ffdb08f207，'
                                '93 个文件有 334 种不同前缀）。实机反馈：整条文本跨过场互换会让'
                                '字幕偏晚、出现时长不对、有些直接消失，等长也一样。')
        self.voice = QtWidgets.QCheckBox('另外打乱原始语音文件（会破坏口型）')
        self.voice.setChecked(False)
        self.voice.setToolTip('纯粹的额外混乱，默认关闭。')
        self.voice.toggled.connect(self._sync)
        self.field = QtWidgets.QCheckBox(
            '随机场内 / NPC 对话（未实机验证；关掉则场内对话不生效）')
        self.field.setChecked(True)
        self.field.setToolTip('场内对话是压缩存放的，打开才会把打乱后的脚本写回去。')
        self.field.toggled.connect(self._sync)
        f.addRow(self.text)
        f.addRow('CG 字幕范围', self.cg_mode)
        f.addRow(self.voice)
        f.addRow(self.field)

        self.varlen = QtWidgets.QCheckBox(
            '允许不同长度的场内对话互换（实验性，未验证）')
        self.varlen.setChecked(False)
        self.varlen.setToolTip('默认只让长度相同的纯文本片段互换。打开后允许长短不同的片段互相'
                               '顶替：整个消息块会重新排布，但块大小不变，所以 .bf 和 .LB 容器'
                               '都不变大。CG 字幕不支持这一套（会错时间）。')
        self.varlen.toggled.connect(self._sync)
        f.addRow(self.varlen)

        self.voice_scope = QtWidgets.QComboBox()
        self.voice_scope.addItems(['global — 全游戏范围', 'per_file — 只在同一个文件内'])
        f.addRow('语音打乱范围', self.voice_scope)

        self.mantra = QtWidgets.QCheckBox('曼斗罗（Mantra）技能随机 —— 已实机验证')
        self.mantra.setChecked(True)
        self.mantra.setToolTip('每个曼斗罗掌握后教什么技能、给什么属性奖励都会重新分配。')
        self.mantra.toggled.connect(self._sync)
        self.mantra_mode = QtWidgets.QComboBox()
        self.mantra_mode.addItems([
            '打散重发 — 全部技能进一个池子，按数量重新分配（推荐，最随机）',
            '整组互换 — 曼斗罗之间整套技能互换（旧行为）',
        ])
        self.mantra_mode.currentIndexChanged.connect(self._sync)
        self.mantra_cost = QtWidgets.QCheckBox('同时随机曼斗罗的学习消耗（Karma）')
        self.mantra_cost.setChecked(False)
        self.mantra_cost.toggled.connect(self._sync)
        f.addRow(self.mantra)
        f.addRow('技能随机方式', self.mantra_mode)
        f.addRow(self.mantra_cost)

        self.varuna = QtWidgets.QCheckBox(
            '把主人公（萨弗）的恶魔形态「伐楼罗」换成「黑伐楼罗」')
        self.varuna.setChecked(False)
        self.varuna.setToolTip('只换外观，不影响能力。')
        self.varuna.toggled.connect(self._sync)
        f.addRow(self.varuna)

        note = QtWidgets.QLabel(
            '字幕只与长度完全相同的另一条互换，少数找不到搭档的会保持原样；'
            '字幕和语音不再同步。<br>'
            '场内对话只替换纯文本，指向立绘 / 模型的控制码不动。')
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _tab_adv(self):
        w = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(w)
        f.addRow(QtWidgets.QLabel('<b>本次将执行：</b>'))
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        f.addRow(self.summary)

        note = QtWidgets.QLabel(
            '命令行等价入口：<code>python dds2_randomizer.py --help</code><br>'
            '自检：<code>python selftest.py</code>')
        note.setWordWrap(True)
        f.addRow(note)
        return w

    # -------------------------------------------------------------- sync
    def _sync(self):
        self.variety_lbl.setText('%d%%' % self.variety.value())
        parts = []
        if self.enc.isChecked():
            parts.append('普通遭遇敌人互换')
        if self.skills.isChecked():
            parts.append('技能列表跟随')
        if self.ai.isChecked():
            parts.append('AI 出招跟随')
        if self.text.isChecked():
            parts.append('CG 字幕随机（%s）' % self.cg_mode.currentText().split(' ')[0])
        if self.varlen.isChecked():
            parts.append('场内对话允许不同长度')
        if self.voice.isChecked():
            parts.append('原始语音打乱')
        if self.mantra.isChecked():
            mode = '打散重发' if self.mantra_mode.currentIndex() == 0 else '整组互换'
            parts.append('曼斗罗技能随机（%s）' % mode
                         + ('（含消耗）' if self.mantra_cost.isChecked() else ''))
        if self.varuna.isChecked():
            parts.append('主角恶魔形态换成黑伐楼罗')
        if self.reveal.isChecked():
            parts.append('Boss 可被分析')
        txt = '、'.join(parts) if parts else '什么都不做（只会复制 ISO）'
        self.summary.setText(txt)

    # ------------------------------------------------------------ picking
    def _pick_iso(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择原版 ISO', HERE, 'ISO 镜像 (*.iso *.bin);;所有文件 (*)')
        if p:
            self.iso.setText(p)
            if self.out.text().endswith('DDS2_random.iso'):
                self.out.setText(os.path.join(
                    os.path.dirname(p), 'DDS2_random.iso'))

    def _pick_out(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '输出 ISO', self.out.text() or HERE, 'ISO 镜像 (*.iso)')
        if p:
            self.out.setText(p)

    def _pick_ban(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '选择禁止出场名单', HERE, '名单 (*.json *.txt);;所有文件 (*)')
        if p:
            self.ban_file.setText(p)

    def _load_cfg(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, '载入配置', HERE, 'JSON (*.json);;所有文件 (*)')
        if not p:
            return
        cfg = Config()
        try:
            cfg.load(p)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, '载入失败', str(exc))
            return
        self._apply(cfg)
        self._log('config loaded: %s' % p)
        self._sync()

    def _save_cfg(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, '保存配置', os.path.join(HERE, 'randomizer_config.json'), 'JSON (*.json)')
        if not p:
            return
        try:
            self._config().save(p)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, '保存失败', str(exc))
            return
        self._log('config saved: %s' % p)

    def _apply(self, cfg):
        self.enc.setChecked(bool(cfg.randomize_encounters))
        self.skills.setChecked(bool(cfg.randomize_enemy_skills))
        self.ai.setChecked(bool(cfg.remap_enemy_ai))
        self.table.setChecked(bool(cfg.write_enemy_table))
        self.text.setChecked(bool(cfg.shuffle_text))
        self.voice.setChecked(bool(cfg.shuffle_voice))
        self.field.setChecked(bool(getattr(cfg, 'field_text', True)))
        self.varlen.setChecked(bool(getattr(cfg, 'field_varlen', False)))
        self.cg_mode.setCurrentIndex(
            {'global': 0, 'file': 1, 'off': 2}.get(
                getattr(cfg, 'text_cg_mode', 'global'), 0))
        self.mantra.setChecked(bool(getattr(cfg, 'randomize_mantras', False)))
        self.mantra_mode.setCurrentIndex(
            1 if getattr(cfg, 'mantra_swap_mode', 'skills') == 'sets' else 0)
        self.mantra_cost.setChecked(bool(getattr(cfg, 'mantra_shuffle_cost', False)))
        self.varuna.setChecked(bool(getattr(cfg, 'black_varuna', False)))
        self.reveal.setChecked(bool(cfg.reveal_enemies))
        self.variety.setValue(int(round(float(cfg.encounter_variety) * 100)))
        self.tol.setValue(float(cfg.enemy_power_tolerance))
        self.stats_mode.setCurrentIndex(1 if cfg.stats_mode == 'scale' else 0)
        self.voice_scope.setCurrentIndex(1 if cfg.shuffle_voice_scope == 'per_file' else 0)
        self.ban_file.setText(cfg.ban_file or '')
        self.ban_ids.setText(','.join(str(x) for x in (cfg.ban_enemies or [])))
        self.backup.setChecked(bool(cfg.backup))
        self.verify.setChecked(bool(cfg.verify))

    def _config(self):
        cfg = Config()
        cfg.randomize_encounters = self.enc.isChecked()
        cfg.randomize_enemy_skills = self.skills.isChecked()
        cfg.remap_enemy_ai = self.ai.isChecked()
        cfg.write_enemy_table = self.table.isChecked()
        # story-battle shuffle is shielded (see ddstool/config.py); the GUI no
        # longer exposes it, and a config file loaded here cannot turn it on
        cfg.shuffle_bosses = False
        cfg.shuffle_text = self.text.isChecked()
        cfg.shuffle_voice = self.voice.isChecked()
        cfg.field_text = self.field.isChecked()
        cfg.field_varlen = self.varlen.isChecked()
        cfg.text_cg_mode = ['global', 'file', 'off'][self.cg_mode.currentIndex()]
        cfg.randomize_mantras = self.mantra.isChecked()
        cfg.mantra_swap_mode = 'sets' if self.mantra_mode.currentIndex() == 1 else 'skills'
        cfg.mantra_shuffle_cost = self.mantra_cost.isChecked() and self.mantra.isChecked()
        cfg.black_varuna = self.varuna.isChecked()
        cfg.shuffle_voice_scope = 'per_file' if self.voice_scope.currentIndex() == 1 else 'global'
        cfg.reveal_enemies = self.reveal.isChecked()
        cfg.encounter_variety = self.variety.value() / 100.0
        cfg.enemy_power_tolerance = self.tol.value()
        cfg.stats_mode = 'scale' if self.stats_mode.currentIndex() == 1 else 'copy'
        cfg.ban_file = self.ban_file.text().strip() or None
        cfg.ban_enemies = [x.strip() for x in self.ban_ids.text().split(',') if x.strip()]
        cfg.backup = self.backup.isChecked()
        cfg.verify = self.verify.isChecked()
        txt = self.seed.text().strip()
        cfg.seed = int(txt) if txt else random.randrange(1, 2 ** 31)
        return cfg

    # ------------------------------------------------------------- run
    def _log(self, s):
        self.log.appendPlainText(s)
        sb = self.log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _start(self):
        if self.worker is not None and self.worker.isRunning():
            return
        src = self.iso.text().strip()
        out = self.out.text().strip()
        if not os.path.isfile(src):
            QtWidgets.QMessageBox.critical(self, '找不到输入', '原版 ISO 不存在：\n%s' % src)
            return
        if os.path.abspath(src) == os.path.abspath(out):
            QtWidgets.QMessageBox.critical(self, '输出不能等于输入', '请换一个输出文件名。')
            return
        if os.path.exists(out):
            r = QtWidgets.QMessageBox.question(
                self, '输出已存在', '%s\n\n已存在，要覆盖吗？' % out,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
            if r != QtWidgets.QMessageBox.Yes:
                return
        cfg = self._config()
        self.log.clear()
        self._log('seed: %d' % cfg.seed)
        self._log('options: %s' % cfg.summary())
        self.start.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText('正在构建…（复制 ISO 需要一点时间）')
        self.worker = Worker(src, out, cfg)
        self.worker.message.connect(self._log)
        self.worker.finished_ok.connect(self._done)
        self.worker.failed.connect(self._fail)
        self.worker.start()

    def _done(self, stats):
        self._log('--- statistics ---')
        for k in sorted(stats):
            self._log('%-28s %s' % (k, stats[k]))
        self.bar.setVisible(False)
        self.start.setEnabled(True)
        self.status.setText('完成：%s' % self.out.text())
        self._sync()
        QtWidgets.QMessageBox.information(
            self, '完成', 'ISO 已生成：\n%s\n\nseed: %s' % (self.out.text(), self.seed.text() or '(随机)'))

    def _fail(self, text):
        self._log(text)
        self.bar.setVisible(False)
        self.start.setEnabled(True)
        self.status.setText('失败 —— 详见日志')
        QtWidgets.QMessageBox.critical(self, '构建失败', text.strip().splitlines()[-1])


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication(sys.argv)
    w = Window()
    w.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
