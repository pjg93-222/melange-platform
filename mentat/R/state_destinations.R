# Alternative geographical partitions of receipt money. Candidate links never
# allocate a receipt among several candidates or trace subsequent PAC spending.
mentat_destination_fields <- c("recipient_state", "represented_state",
  "recipient_location_status", "represented_location_status",
  "recipient_location_evidence", "represented_location_evidence")

mentat_state_value <- function(value) {
  value <- toupper(trimws(ifelse(is.na(value), "", value)))
  ifelse(!nzchar(value), "Unknown", ifelse(value %in% c(state.abb,"DC","PR","VI","GU","AS","MP","FM","MH","PW"), value, paste0("Other / ",value)))
}

mentat_add_destinations <- function(rows, committees, candidates, links) {
  rows <- data.table::copy(rows)
  master <- data.table::copy(committees)
  if (!"state" %in% names(master)) master[, state := ""]
  master[, destination := mentat_state_value(state)]
  master <- master[, .(recipient_state=if(data.table::uniqueN(destination)==1L) destination[[1L]] else "Ambiguous",
    recipient_location_evidence=paste(sort(unique(source_url)),collapse="; ")),
    by=.(cycle,committee_id)]
  mapped <- merge(unique(links[designation %in% c("P","A"), .(cycle,committee_id,candidate_id,source_url)]),
    candidates[, .(cycle,candidate_id,office,office_state,candidate_source=source_url)],
    by=c("cycle","candidate_id"),all.x=TRUE,allow.cartesian=TRUE,sort=FALSE)
  mapped[, destination := ifelse(!is.na(office)&office=="P","National / N/A",mentat_state_value(office_state))]
  mapped <- mapped[, .(represented_state=if(data.table::uniqueN(candidate_id)>1L||data.table::uniqueN(destination)>1L) "Ambiguous" else destination[[1L]],
    represented_location_evidence=paste(sort(unique(c(source_url,candidate_source)[!is.na(c(source_url,candidate_source))])),collapse="; ")),
    by=.(cycle,committee_id)]
  keys <- paste(rows$cycle,rows$committee_id,sep="|")
  for (mapping in list(master,mapped)) {
    at <- match(keys,paste(mapping$cycle,mapping$committee_id,sep="|"))
    for (field in setdiff(names(mapping),c("cycle","committee_id"))) data.table::set(rows,j=field,value=mapping[[field]][at])
  }
  for (field in c("recipient_state","represented_state")) {
    rows[is.na(get(field))|!nzchar(get(field)),(field):="Unknown"]
    status <- sub("_state$","_location_status",field)
    rows[,(status):=ifelse(get(field)=="Unknown","unknown",ifelse(get(field)=="Ambiguous","ambiguous",ifelse(get(field)=="National / N/A","national",ifelse(startsWith(get(field),"Other / "),"other","known"))))]
  }
  for(field in c("recipient_location_evidence","represented_location_evidence"))rows[is.na(get(field)),(field):="Location unavailable for this cycle"]
  rows[]
}

mentat_state_destinations <- function(rows) {
  eligible <- rows[include_in_analysis=="Yes"]
  result <- data.table::rbindlist(lapply(c("recipient_state","represented_state"),function(field) {
    value <- eligible[, .(net_amount=sum(round(amount*100))/100,receipt_count=.N),
      by=.(cycle,cycle_label,company,party,destination=get(field))]
    value[, `:=`(location_view=field,accounting_basis="Included receipts")]
    value
  }))
  expected <- sum(round(eligible$amount*100))
  for(field in c("recipient_state","represented_state"))if(sum(round(result[location_view==field]$net_amount*100))!=expected)stop("State destination totals do not reconcile")
  result[]
}
