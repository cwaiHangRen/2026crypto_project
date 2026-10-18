# Round9 六个 ContentID 恢复失败样本有界诊断

生产 G3 在发布 gate 之前失败，没有产生最终 Manifest，因此生产 ContentID 不可得。本诊断为每个样本使用稳定的诊断 ContentID，逐样本最多运行 v1+D0、v1+S0、既有 v2-residual 三种配置；只有最终编码字节的 direct D0 恢复等于诊断 ContentID 才记为 gate 通过。

|样本|原始尺寸/格式|诊断 CID|配置|最终编码/强度|候选 ContentID|RS/同步/MSE 摘要|结论|
|---|---|---|---|---|---|---|---|
|r8-img-0058|[800, 624] / PNG|468a7d05a128c615ce6037237fbd9c87|v1+D0|PNG / 34.0|无|RS/CRC=not recovered|FAIL|
|r8-img-0058|[800, 624] / PNG|468a7d05a128c615ce6037237fbd9c87|v1+S0|PNG / 34.0|无|S0=TIME_BUDGET_EXHAUSTED; stop=time_budget_exhausted; attempts=48|FAIL|
|r8-img-0058|[800, 624] / PNG|468a7d05a128c615ce6037237fbd9c87|existing-v2-candidate|PNG / 34.0|无|RS=False/False; bit_err=24; MSE系数误差=10.581|FAIL|
|r8-img-0142|[1440, 624] / PNG|b6fd5e6da516d11731b290c1ece79f35|v1+D0|PNG / 34.0|b6fd5e6da516d11731b290c1ece79f35|RS/CRC=recovered|PASS|
|r8-img-0142|[1440, 624] / PNG|b6fd5e6da516d11731b290c1ece79f35|v1+S0|PNG / 34.0|b6fd5e6da516d11731b290c1ece79f35|S0=FOUND; stop=d0_found; attempts=0|PASS|
|r8-img-0142|[1440, 624] / PNG|b6fd5e6da516d11731b290c1ece79f35|existing-v2-candidate|PNG / 34.0|b6fd5e6da516d11731b290c1ece79f35|RS=True/True; bit_err=20; MSE系数误差=10.072|PASS|
|r8-img-0178|[800, 768] / PNG|889ea910115a39316f4245ee85a0840e|v1+D0|PNG / 34.0|889ea910115a39316f4245ee85a0840e|RS/CRC=recovered|PASS|
|r8-img-0178|[800, 768] / PNG|889ea910115a39316f4245ee85a0840e|v1+S0|PNG / 34.0|889ea910115a39316f4245ee85a0840e|S0=FOUND; stop=d0_found; attempts=0|PASS|
|r8-img-0178|[800, 768] / PNG|889ea910115a39316f4245ee85a0840e|existing-v2-candidate|PNG / 34.0|无|RS=False/False; bit_err=22; MSE系数误差=9.966|FAIL|
|r8-img-0226|[800, 624] / PNG|ef42f386d88db17275106014ee5f2aab|v1+D0|PNG / 34.0|无|RS/CRC=not recovered|FAIL|
|r8-img-0226|[800, 624] / PNG|ef42f386d88db17275106014ee5f2aab|v1+S0|PNG / 34.0|无|S0=TIME_BUDGET_EXHAUSTED; stop=time_budget_exhausted; attempts=56|FAIL|
|r8-img-0226|[800, 624] / PNG|ef42f386d88db17275106014ee5f2aab|existing-v2-candidate|PNG / 34.0|无|RS=False/False; bit_err=29; MSE系数误差=10.973|FAIL|
|r8-img-0394|[800, 624] / PNG|72b707a1725cea6967c0a3c53153ab75|v1+D0|PNG / 34.0|72b707a1725cea6967c0a3c53153ab75|RS/CRC=recovered|PASS|
|r8-img-0394|[800, 624] / PNG|72b707a1725cea6967c0a3c53153ab75|v1+S0|PNG / 34.0|72b707a1725cea6967c0a3c53153ab75|S0=FOUND; stop=d0_found; attempts=0|PASS|
|r8-img-0394|[800, 624] / PNG|72b707a1725cea6967c0a3c53153ab75|existing-v2-candidate|PNG / 34.0|72b707a1725cea6967c0a3c53153ab75|RS=True/True; bit_err=16; MSE系数误差=9.399|PASS|
|r8-img-0520|[1760, 624] / PNG|c8dfcdfc16df5c89fc9106f43a54023a|v1+D0|PNG / 34.0|c8dfcdfc16df5c89fc9106f43a54023a|RS/CRC=recovered|PASS|
|r8-img-0520|[1760, 624] / PNG|c8dfcdfc16df5c89fc9106f43a54023a|v1+S0|PNG / 34.0|c8dfcdfc16df5c89fc9106f43a54023a|S0=FOUND; stop=d0_found; attempts=0|PASS|
|r8-img-0520|[1760, 624] / PNG|c8dfcdfc16df5c89fc9106f43a54023a|existing-v2-candidate|PNG / 34.0|c8dfcdfc16df5c89fc9106f43a54023a|RS=True/True; bit_err=13; MSE系数误差=8.587|PASS|

结论：v1+D0 在诊断 CID 下对 4 个样本可恢复，对 r8-img-0058 和 r8-img-0226 仍失败；v1+S0 没有为这两个样本提供恢复，并在既有预算内停止；既有 v2-residual 对 0058、0226、0178 未恢复。其他样本的诊断成功不等于原 Round9 生产 CID 成功。没有修改生产配置、gate 或 Round9 统计，也没有删除失败证据。
