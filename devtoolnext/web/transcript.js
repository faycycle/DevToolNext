/* Display projection only. Raw records remain authoritative and unchanged. */
(function(root){
  const MAX_LINE=16384;
  const bytesOf = record => Uint8Array.from(atob(record.data_base64), c => c.charCodeAt(0));
  const hexOf = bytes => [...bytes].map(b => b.toString(16).padStart(2, '0').toUpperCase()).join(' ');
  function attach(row, record){ if(!row.records.includes(record)) row.records.push(record) }
  function blank(decoder){ return {decoder, row:null, carry:[], last:null, skipLF:false} }
  function openRow(state, rows, record){
    const row={direction:record.direction, wall:state.carry.length?state.carry[0].wall:record.wall,
      text:'', records:[], truncated:false};
    // Conservative evidence: the previous chunk may hold bytes of this line.
    if(state.last)attach(row, state.last);
    for(const previous of state.carry)attach(row, previous);
    rows.push(row); state.row=row; return row;
  }
  function rowsOf(records, mode, encoding) {
    if(mode !== 'log') return records.map(r => ({direction:r.direction, wall:r.wall,
      text:hexOf(bytesOf(r)), records:[r], truncated:false}));
    const state={rx:blank(new TextDecoder(encoding)), tx:blank(new TextDecoder(encoding))};
    const rows=[];
    for(const record of records){
      const s=state[record.direction];
      if(!s)continue;
      const text=s.decoder.decode(bytesOf(record),{stream:true});
      if(!text){s.carry.push(record);s.last=record;continue}
      let i=0;
      // A CR ended the previous chunk; drop the LF that completes that CRLF pair.
      if(s.skipLF){s.skipLF=false;if(text.charCodeAt(0)===10)i=1}
      for(;i<text.length;i++){
        const code=text.charCodeAt(i);
        if(code===13||code===10){
          if(!s.row)openRow(s, rows, record);
          attach(s.row, record);
          s.row=null;
          if(code===13){
            if(i+1<text.length){if(text.charCodeAt(i+1)===10)i++}
            else s.skipLF=true;
          }
          continue;
        }
        if(!s.row)openRow(s, rows, record);
        attach(s.row, record);
        if(s.row.text.length<MAX_LINE)s.row.text+=text[i];else s.row.truncated=true;
      }
      s.carry=[];s.last=record;
    }
    return rows.slice(-1500).map(row=>({...row,records:[...row.records].sort((a,b)=>a.id-b.id)}));
  }
  const api={bytesOf,hexOf,rowsOf};root.DevToolTranscript=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(globalThis);
