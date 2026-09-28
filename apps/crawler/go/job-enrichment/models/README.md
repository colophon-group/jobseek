# Pinned language model and attribution

`lid.176.ftz` is the unmodified fastText compressed language identification
model by Facebook, Inc. / Armand Joulin, Edouard Grave, Piotr Bojanowski,
Tomas Mikolov, Matthijs Douze and Hervé Jégou. It recognizes 176 languages.
Model source and license: https://fasttext.cc/docs/en/language-identification.html
Original asset: https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz
License: Creative Commons Attribution-ShareAlike 3.0 Unported, reproduced in
`MODEL-LICENSE`: https://creativecommons.org/licenses/by-sa/3.0/

References: *Bag of Tricks for Efficient Text Classification* (2016),
https://arxiv.org/abs/1607.01759; *FastText.zip: Compressing text classification
models* (2016), https://arxiv.org/abs/1612.03651.

The exact 938,013 bytes are copied from the existing locked `fast-langdetect`
1.0.1 package, SHA-256
`8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83`.
Go embeds the asset and checks its digest before decoding it; runtime performs
no model download. The asset is unchanged and available separately under its
own license. These notices are also shipped in the crawler image.

The Go prediction/decoding code in `../language.go` is adapted from the
Facebook, Inc. MIT-licensed prediction implementation shipped in the locked
`fasttext-predict` 0.9.2.4 source. Its copyright/license is reproduced in
`FASTTEXT-LICENSE`. Source archive SHA-256:
`18a6fb0d74c7df9280db1f96cb75d990bfd004fa9d669493ea3dd3d54f84dbc7`.
Source: https://pypi.org/project/fasttext-predict/0.9.2.4/

The independent Go code and retained model have separate license notices.
