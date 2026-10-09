// Entry point of static/codemirror.js, the CodeMirror 6 bundle used by the student editor.
// Rebuild it with ./build-codemirror.sh after changing this file or the versions there.
export {EditorView, keymap, lineNumbers, drawSelection, ViewPlugin, Decoration} from "@codemirror/view";
export {EditorState, RangeSetBuilder} from "@codemirror/state";
export {indentUnit, indentOnInput, bracketMatching, syntaxHighlighting, syntaxTree, HighlightStyle} from "@codemirror/language";
export {defaultKeymap, history, historyKeymap, deleteCharBackwardStrict} from "@codemirror/commands";
export {python} from "@codemirror/lang-python";
export {tags} from "@lezer/highlight";
